/*
--------------------------------------------------------------------
Skin manage: propose skel nodes for skinning by surface proximity.
Aligned with NDTools addBoneToSkin (signed dist + core AABB + half outsideLimit).
Independent of MCP_SceneManage. Load via fileIn (autostart loads all mcp/*.ms).
Listener:
	MCP_SkinManage.excludePrefixes = #("v_")
	MCP_SkinManage.proposeSkinBones #(meshHandle) padRatio:0.05
--------------------------------------------------------------------
*/
global MCP_SkinManage
-- ---------------------------------------------------------------------------
-- Helpers (file-local globals; SkinManage_ prefix)
-- ---------------------------------------------------------------------------
fn SkinManage_IsSkinBoneNode o =
(
	o != undefined and ((isKindOf o BoneGeometry) or (isKindOf o Biped_Object))
)
fn SkinManage_IsExcludedName nodeName prefixes:#("v_") =
(
	if nodeName == undefined do return false
	local low = toLower (nodeName as string)
	if prefixes == undefined do prefixes = #("v_")
	for pfx in prefixes do
	(
		local p = toLower (pfx as string)
		if p.count > 0 and (findString low p) == 1 do return true
	)
	false
)
fn SkinManage_IsCandidateNode o prefixes:#("v_") =
(
	(SkinManage_IsSkinBoneNode o) and (not (SkinManage_IsExcludedName o.name prefixes:prefixes))
)
fn SkinManage_JsonEscapeName s =
(
	s = s as string
	s = substituteString s "\\" "\\\\"
	s = substituteString s "\"" "\\\""
	s
)
fn SkinManage_FloatStr f =
(
	local n = 0.0
	try (n = f as float) catch (n = 0.0)
	((n * 1000.0) as integer / 1000.0) as string
)
fn SkinManage_WorldBBox node =
(
	if node == undefined do return undefined
	local bb = undefined
	try (bb = nodeGetBoundingBox node (matrix3 1)) catch (bb = undefined)
	if bb == undefined do return undefined
	#(bb[1], bb[2])
)
fn SkinManage_ExpandBox bmin bmax pad =
(
	#([bmin.x - pad, bmin.y - pad, bmin.z - pad], [bmax.x + pad, bmax.y + pad, bmax.z + pad])
)
fn SkinManage_PointToAabbDist p bmin bmax =
(
	local dx = 0.0
	local dy = 0.0
	local dz = 0.0
	if p.x < bmin.x then dx = bmin.x - p.x else if p.x > bmax.x then dx = p.x - bmax.x
	if p.y < bmin.y then dy = bmin.y - p.y else if p.y > bmax.y then dy = p.y - bmax.y
	if p.z < bmin.z then dz = bmin.z - p.z else if p.z > bmax.z then dz = p.z - bmax.z
	sqrt (dx * dx + dy * dy + dz * dz)
)
fn SkinManage_AabbOverlap aMin aMax bMin bMax =
(
	not (aMax.x < bMin.x or aMin.x > bMax.x or aMax.y < bMin.y or aMin.y > bMax.y or aMax.z < bMin.z or aMin.z > bMax.z)
)
fn SkinManage_AabbCenter bmin bmax =
(
	(bmin + bmax) * 0.5
)
fn SkinManage_PointInAabb p bmin bmax =
(
	p.x >= bmin.x and p.x <= bmax.x and \
	p.y >= bmin.y and p.y <= bmax.y and \
	p.z >= bmin.z and p.z <= bmax.z
)
-- Shrink mesh AABB so "inside" claims must sit in the core volume
-- (rejects floating bones that only sit inside an oversized dress world box).
fn SkinManage_MeshCoreAabb meshMin meshMax pad =
(
	local p = pad as float
	if p < 0 do p = 0
	local cmin = [meshMin.x + p, meshMin.y + p, meshMin.z + p]
	local cmax = [meshMax.x - p, meshMax.y - p, meshMax.z - p]
	if cmin.x > cmax.x or cmin.y > cmax.y or cmin.z > cmax.z do
		return #(meshMin, meshMax)
	#(cmin, cmax)
)
-- Start = bone pivot; end = first candidate child's pivot (or bone length for leaves).
fn SkinManage_AxisEndpoints _skelNode prefixes:#("v_") =
(
	local p0 = _skelNode.transform.pos
	try (p0 = _skelNode.transform.pos) catch (try (p0 = _skelNode.transform.translation) catch ())
	local skelKids = for c in _skelNode.children where (SkinManage_IsCandidateNode c prefixes:prefixes) collect c
	if skelKids.count > 0 do
	(
		local p1 = skelKids[1].transform.pos
		try (p1 = skelKids[1].transform.pos) catch (try (p1 = skelKids[1].transform.translation) catch ())
		return #(p0, p1)
	)
	if isKindOf _skelNode BoneGeometry do
	(
		local len = 0.0
		try (len = _skelNode.length as float) catch (len = 0.0)
		if len > 0.0001 do
		(
			local xAxis = [1, 0, 0]
			try (xAxis = normalize _skelNode.transform.row1) catch ()
			return #(p0, p0 + xAxis * len)
		)
	)
	#(p0, p0)
)
fn SkinManage_BuildMeshGrids meshNodes =
(
	local grids = #()
	for m in meshNodes do
	(
		local g = undefined
		try
		(
			g = RayMeshGridIntersect()
			g.Initialize 72
			g.addNode m
			g.buildGrid()
			append grids g
		)
		catch
		(
			try (if g != undefined do g.free()) catch ()
		)
	)
	grids
)
fn SkinManage_FreeMeshGrids grids =
(
	if grids == undefined do return()
	for g in grids do
	(
		try (g.free()) catch ()
	)
)
-- Returns #(sd, fd, dotOrUndefined, branch) branch:#inside|#outside|#no_normal|#fail
-- RayMeshGridIntersect: closestFace -> getClosestHit -> getHitPoint/getHitNormal/getHitDist
fn SkinManage_SignedDistDetail g pos searchDist =
(
	if g == undefined do return #(undefined, undefined, undefined, #fail)
	local nHit = 0
	try (nHit = g.closestFace pos) catch (nHit = 0)
	if nHit == undefined or nHit < 1 do return #(undefined, undefined, undefined, #fail)
	local idx = 1
	try (idx = g.getClosestHit()) catch (idx = 1)
	if idx == undefined or idx < 1 do idx = 1
	local fd = -1.0
	try (fd = g.getHitDist idx) catch (fd = -1.0)
	if fd == undefined or fd < 0 do
	(
		try (fd = g.getPerpDist idx) catch (fd = -1.0)
	)
	if fd == undefined or fd < 0 do return #(undefined, undefined, undefined, #fail)
	local hp = undefined
	local hn = undefined
	try (hp = g.getHitPoint idx) catch (hp = undefined)
	try (hn = g.getHitNormal idx) catch (hn = undefined)
	if hp == undefined or hn == undefined do return #(fd, fd, undefined, #no_normal)
	local nl = length hn
	if nl < 0.000001 do return #(fd, fd, undefined, #no_normal)
	local dv = dot (pos - hp) (hn / nl)
	if dv < 0.0 then
		#((-fd), fd, dv, #inside)
	else
		#(fd, fd, dv, #outside)
)
-- Across grids: prefer any inside (most negative), else nearest outside.
fn SkinManage_SignedDistDetailMeshes grids pos searchDist =
(
	if grids == undefined or grids.count == 0 do return #(undefined, undefined, undefined, #fail)
	local bestInside = undefined
	local bestOutside = undefined
	local bestInDetail = undefined
	local bestOutDetail = undefined
	for g in grids do
	(
		local det = SkinManage_SignedDistDetail g pos searchDist
		local s = det[1]
		if s != undefined do
		(
			if s < 0.0 then
			(
				if bestInside == undefined or s < bestInside do
				(
					bestInside = s
					bestInDetail = det
				)
			)
			else
			(
				if bestOutside == undefined or s < bestOutside do
				(
					bestOutside = s
					bestOutDetail = det
				)
			)
		)
	)
	if bestInDetail != undefined then bestInDetail
	else if bestOutDetail != undefined then bestOutDetail
	else #(undefined, undefined, undefined, #fail)
)
-- Keep when: confirmed inside (sd<0 and sample in mesh core AABB),
-- or outside surface distance <= outsideLimit (maxDist * 0.5).
-- Samples: pivot, end (first child / leaf length), bone world-AABB center.
fn SkinManage_BoneContactsMeshGrid grids boneNode maxDist searchDist meshMin meshMax prefixes:#("v_") =
(
	if grids == undefined or grids.count == 0 do return #(false, -1.0)
	local bb = SkinManage_WorldBBox boneNode
	if bb == undefined do return #(false, -1.0)
	local bnMin = bb[1]
	local bnMax = bb[2]
	local expBone = SkinManage_ExpandBox bnMin bnMax maxDist
	if not (SkinManage_AabbOverlap expBone[1] expBone[2] meshMin meshMax) do
	(
		local cd = SkinManage_PointToAabbDist (SkinManage_AabbCenter bnMin bnMax) meshMin meshMax
		return #(false, cd)
	)
	local meshDiag = distance meshMin meshMax
	if meshDiag < 0.0001 do meshDiag = 0.0001
	local corePad = amax #(maxDist, meshDiag * 0.05)
	local core = SkinManage_MeshCoreAabb meshMin meshMax corePad
	local coreMin = core[1]
	local coreMax = core[2]
	local outsideLimit = maxDist * 0.5
	local ends = SkinManage_AxisEndpoints boneNode prefixes:prefixes
	local boxC = SkinManage_AabbCenter bnMin bnMax
	local pts = #(ends[1], ends[2], boxC)
	local insideCount = 0
	local bestOutside = undefined
	local bestSigned = undefined
	for p in pts do
	(
		local det = SkinManage_SignedDistDetailMeshes grids p searchDist
		local s = det[1]
		local eff = s
		if eff != undefined and eff < 0 and (not (SkinManage_PointInAabb p coreMin coreMax)) do
			eff = abs eff
		if eff != undefined do
		(
			if bestSigned == undefined then
				bestSigned = eff
			else if eff < bestSigned do
				bestSigned = eff
			if eff < 0 then
				insideCount += 1
			else
			(
				if bestOutside == undefined then
					bestOutside = eff
				else if eff < bestOutside do
					bestOutside = eff
			)
		)
	)
	if bestSigned == undefined do return #(false, -1.0)
	local keep = (insideCount >= 1) or \
		(bestOutside != undefined and bestOutside <= outsideLimit)
	local reportDist = if insideCount >= 1 then (abs bestSigned) \
		else if bestOutside != undefined then bestOutside \
		else (abs bestSigned)
	if keep then
		#(true, reportDist)
	else
		#(false, reportDist)
)
fn SkinManage_HandleInArray arr h =
(
	local hi = h
	try (hi = hi as integer) catch ()
	if arr == undefined do return false
	for x in arr do
	(
		local xi = x
		try (xi = xi as integer) catch ()
		if xi == hi do return true
	)
	false
)
struct SkinManageStruct
(
	-- Name prefixes to skip (case-insensitive), e.g. helper joints "v_".
	excludePrefixes = #("v_"),
	fn stringArrayToJson arr =
	(
		local str_body = stringstream ""
		if arr == undefined or arr.count == 0 then
		(
			format "[]" to:str_body
			return (str_body as string)
		)
		format "[" to:str_body
		for i = 1 to arr.count do
		(
			if i != arr.count then
				format "\"%\"," arr[i] to:str_body
			else
				format "\"%\"" arr[i] to:str_body
		)
		format "]" to:str_body
		str_body as string
	),
	fn intarrayToJson arr =
	(
		local str_body = stringstream ""
		if arr == undefined or arr.count == 0 then
		(
			format "[]" to:str_body
			return (str_body as string)
		)
		format "[" to:str_body
		for i = 1 to arr.count do
		(
			local n = arr[i]
			try (n = n as integer) catch ()
			if i != arr.count then
				format "%," n to:str_body
			else
				format "%" n to:str_body
		)
		format "]" to:str_body
		str_body as string
	),
	fn findNodeByHandle h =
	(
		local hi = h
		try (hi = h as integer) catch ()
		local obj = undefined
		try (obj = maxOps.getNodeByHandle hi) catch (obj = undefined)
		obj
	),
	-- Propose skel nodes for skinning by mesh surface proximity (no Skin weights).
	-- meshHandles: required AnimHandle array.
	-- boneHandles: optional; omit → all unhidden Bone/Biped.
	-- sampleCount: kept for API compat; sampling is fixed 3 points (NDTools-aligned).
	fn proposeSkinBones meshHandles boneHandles:undefined padRatio:0.05 absPadding:0.0 sampleCount:5 =
	(
		local excludePrefixes = this.excludePrefixes
		if excludePrefixes == undefined do excludePrefixes = #("v_")
		if meshHandles == undefined do meshHandles = #()
		local meshNodes = #()
		for h in meshHandles do
		(
			local obj = this.findNodeByHandle h
			if obj != undefined do append meshNodes obj
		)
		if meshNodes.count == 0 do
			return "{\"ok\":false,\"code\":\"BAD_PARAM\",\"error\":\"No valid mesh handles\"}"
		local bmin = undefined
		local bmax = undefined
		for m in meshNodes do
		(
			local bb = SkinManage_WorldBBox m
			if bb != undefined do
			(
				if bmin == undefined then
				(
					bmin = bb[1]
					bmax = bb[2]
				)
				else
				(
					bmin = [amin #(bmin.x, bb[1].x), amin #(bmin.y, bb[1].y), amin #(bmin.z, bb[1].z)]
					bmax = [amax #(bmax.x, bb[2].x), amax #(bmax.y, bb[2].y), amax #(bmax.z, bb[2].z)]
				)
			)
		)
		if bmin == undefined do
			return "{\"ok\":false,\"code\":\"BAD_PARAM\",\"error\":\"Could not read mesh bounding boxes\"}"
		local diag = distance bmin bmax
		if diag < 0.0001 do diag = 0.0001
		local pr = padRatio as float
		if pr < 0 do pr = 0
		local ap = absPadding as float
		if ap < 0 do ap = 0
		local maxDist = amax #(ap, pr * diag)
		local coarsePad = amax #(maxDist * 2.0, diag * 0.05)
		local boneNodes = #()
		local droppedEntries = #()
		local droppedHandles = #()
		if boneHandles != undefined and boneHandles.count > 0 then
		(
			for h in boneHandles do
			(
				local obj = this.findNodeByHandle h
				if SkinManage_IsSkinBoneNode obj do
				(
					if SkinManage_IsExcludedName obj.name prefixes:excludePrefixes then
					(
						local hi = obj.handle
						try (hi = hi as integer) catch ()
						append droppedHandles hi
						append droppedEntries (
							"{\"name\":\"" + (SkinManage_JsonEscapeName obj.name) + "\",\"handle\":" + (hi as string) + \
							",\"reason\":\"name_filter\",\"distance\":null}"
						)
					)
					else
					(
						append boneNodes obj
					)
				)
			)
		)
		else
		(
			for o in objects where (not o.isHidden) do
			(
				if SkinManage_IsSkinBoneNode o do
				(
					if SkinManage_IsExcludedName o.name prefixes:excludePrefixes then
					(
						local hi = o.handle
						try (hi = hi as integer) catch ()
						append droppedHandles hi
						append droppedEntries (
							"{\"name\":\"" + (SkinManage_JsonEscapeName o.name) + "\",\"handle\":" + (hi as string) + \
							",\"reason\":\"name_filter\",\"distance\":null}"
						)
					)
					else
					(
						append boneNodes o
					)
				)
			)
		)
		local grids = SkinManage_BuildMeshGrids meshNodes
		if grids.count == 0 do
			return "{\"ok\":false,\"code\":\"GRID_FAIL\",\"error\":\"Could not build RayMeshGridIntersect for meshes\"}"
		local searchDist = coarsePad + maxDist + diag * 0.5
		if searchDist < 0.001 do searchDist = 0.001
		local nearHandles = #()
		local keptSpatialEntries = #()
		for skelNode in boneNodes do
		(
			local nearInfo = SkinManage_BoneContactsMeshGrid grids skelNode maxDist searchDist bmin bmax prefixes:excludePrefixes
			local isNear = nearInfo[1]
			local bestSurf = nearInfo[2]
			local hi = skelNode.handle
			try (hi = hi as integer) catch ()
			if isNear then
			(
				append nearHandles hi
				append keptSpatialEntries (
					"{\"name\":\"" + (SkinManage_JsonEscapeName skelNode.name) + "\",\"handle\":" + (hi as string) + \
					",\"reason\":\"near_mesh\",\"distance\":" + (SkinManage_FloatStr bestSurf) + "}"
				)
			)
			else
			(
				append droppedHandles hi
				append droppedEntries (
					"{\"name\":\"" + (SkinManage_JsonEscapeName skelNode.name) + "\",\"handle\":" + (hi as string) + \
					",\"reason\":\"far\",\"distance\":" + (SkinManage_FloatStr bestSurf) + "}"
				)
			)
		)
		SkinManage_FreeMeshGrids grids
		local keepHandleSet = #()
		for h in nearHandles do append keepHandleSet h
		local ancestorEntries = #()
		local ancestorHandles = #()
		for skelNode in boneNodes do
		(
			local hi = skelNode.handle
			try (hi = hi as integer) catch ()
			if (SkinManage_HandleInArray nearHandles hi) do
			(
				local p = skelNode.parent
				local guard = 0
				while p != undefined and guard < 512 do
				(
					guard += 1
					if not (SkinManage_IsCandidateNode p prefixes:excludePrefixes) then
					(
						if (SkinManage_IsSkinBoneNode p) and (SkinManage_IsExcludedName p.name prefixes:excludePrefixes) then
							p = p.parent
						else
							p = undefined
					)
					else
					(
						local ph = p.handle
						try (ph = ph as integer) catch ()
						if not (SkinManage_HandleInArray keepHandleSet ph) do
						(
							append keepHandleSet ph
							append ancestorHandles ph
							append ancestorEntries (
								"{\"name\":\"" + (SkinManage_JsonEscapeName p.name) + "\",\"handle\":" + (ph as string) + \
								",\"reason\":\"ancestor\",\"distance\":null}"
							)
						)
						p = p.parent
					)
				)
			)
		)
		local keptJsonParts = #()
		for e in keptSpatialEntries do append keptJsonParts e
		for e in ancestorEntries do append keptJsonParts e
		local bonesNames = #()
		local bonesHandlesOut = #()
		for i = 1 to nearHandles.count do
		(
			local hi = nearHandles[i]
			local nm = ""
			local node = this.findNodeByHandle hi
			if node != undefined do nm = node.name
			append bonesHandlesOut hi
			append bonesNames nm
		)
		for i = 1 to ancestorHandles.count do
		(
			local ph = ancestorHandles[i]
			if not (SkinManage_HandleInArray bonesHandlesOut ph) do
			(
				local node = this.findNodeByHandle ph
				local nm = if node != undefined then node.name else ""
				append bonesHandlesOut ph
				append bonesNames nm
			)
		)
		local warningVal = "null"
		if keptJsonParts.count == 0 and boneNodes.count > 0 do
			warningVal = "\"No bones within surface distance; try larger padRatio or absPadding\""
		local dropFiltered = #()
		for i = 1 to droppedEntries.count do
		(
			if not (SkinManage_HandleInArray keepHandleSet droppedHandles[i]) do
				append dropFiltered droppedEntries[i]
		)
		local keptBody = stringstream ""
		format "[" to:keptBody
		for i = 1 to keptJsonParts.count do
		(
			if i > 1 do format "," to:keptBody
			format "%" keptJsonParts[i] to:keptBody
		)
		format "]" to:keptBody
		local dropBody = stringstream ""
		format "[" to:dropBody
		for i = 1 to dropFiltered.count do
		(
			if i > 1 do format "," to:dropBody
			format "%" dropFiltered[i] to:dropBody
		)
		format "]" to:dropBody
		"{" + \
			"\"ok\":true," + \
			"\"kept\":" + (keptBody as string) + "," + \
			"\"dropped\":" + (dropBody as string) + "," + \
			"\"bones\":" + (this.stringArrayToJson bonesNames) + "," + \
			"\"bones_handle\":" + (this.intarrayToJson bonesHandlesOut) + "," + \
			"\"mesh_diag\":" + (SkinManage_FloatStr diag) + "," + \
			"\"max_dist\":" + (SkinManage_FloatStr maxDist) + "," + \
			"\"warning\":" + warningVal + \
		"}"
	)
)
MCP_SkinManage = SkinManageStruct()
