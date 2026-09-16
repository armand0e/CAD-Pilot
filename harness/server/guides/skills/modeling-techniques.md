# 3D modelling techniques (how to actually build the mesh)

Three ways to make a form; a strong modeller reaches for the right one per part, not one
for everything. Read this to build deliberately rather than piling up spheres.

## The three paradigms

- Primitive + combine + remesh (organic mass): start from primitives, overlap them into
  the rough form, join, and VOXEL REMESH into one clean surface (blender-modeling.md).
  Best for creatures, characters, blobby organic shapes. Fast, always watertight.
- Edit-mesh / box modelling (controlled form): start from a primitive and push its
  geometry - extrude faces, inset, bevel, loop-cut, subdivide - to grow exact shapes.
  Best for hard-surface, clothing, deliberate forms where you control every plane.
- Modifiers (non-destructive, procedural): stack operations that recompute from the
  base - subdivision, mirror, array, bevel, solidify, screw, displace, remesh, decimate.
  Best for repetition, symmetry, thickness, and smoothing without hand-editing.

## Box modelling with bmesh (controlled geometry)

```python
import bpy, bmesh
me = bpy.data.meshes.new('Part'); ob = bpy.data.objects.new('Part', me)
bpy.context.collection.objects.link(ob)
bm = bmesh.new(); bmesh.ops.create_cube(bm, size=20)
top = [f for f in bm.faces if f.normal.z > 0.5][0]
grown = bmesh.ops.extrude_face_region(bm, geom=[top])            # extrude a face...
face = [g for g in grown['geom'] if isinstance(g, bmesh.types.BMFace)][0]
bmesh.ops.translate(bm, vec=(0, 0, 12), verts=face.verts[:])     # ...pull it up
bmesh.ops.inset_individual(bm, faces=[face], thickness=2)        # inset for a rim/panel
bmesh.ops.bevel(bm, geom=list(bm.edges), offset=1.0, segments=2, affect='EDGES')  # soften edges
bm.to_mesh(me); bm.free()
```

The core operations are extrude (grow), inset (panel/border), bevel (soften/round edges),
subdivide (add resolution), and merge/bridge (connect). Build the primary shape from a
box or cylinder by extruding and scaling faces; this is how deliberate forms are made.

## Modifiers as technique

```python
obj = bpy.context.active_object
def add(kind, **p):
    m = obj.modifiers.new(kind.lower(), kind)
    for k, v in p.items(): setattr(m, k, v)
    return m
add('SUBSURF', levels=2)                          # smooth / round (organic)
add('MIRROR', use_axis=(True, False, False))      # symmetry - build one half
add('BEVEL', width=0.6, segments=2)               # rounded edges (hard-surface reads real)
add('SOLIDIFY', thickness=1.5)                    # thickness to an open surface (cloth, shells)
add('ARRAY', count=6, relative_offset_displace=(1.2, 0, 0))  # repeat (fence, teeth, greebles)
add('SCREW', angle=6.283, steps=48)               # revolve a profile (bottles, springs)
add('DECIMATE', ratio=0.5)                        # cut triangle count on a dense mesh
add('REMESH', mode='VOXEL', voxel_size=0.6)       # rebuild one clean watertight manifold
```

BEVEL is the single biggest upgrade to hard-surface believability - nothing in the real
world has a perfectly sharp edge. ARRAY plus a curve or offset builds repetition cheaply.

## Topology, even for procedural work

You are building meshes in code, but the same topology rules decide whether they read
and deform well:
- Quads over triangles where you will SUBSURF or deform - triangles pinch under
  subdivision. Primitives + remesh give even quad-ish density automatically.
- Even density: match resolution to curvature; do not leave one region coarse and another
  fine unless the detail needs it.
- Edge flow follows the form and, for characters, the muscles and joints that bend.
- Keep it as light as it can be and still read - fewer, well-placed polygons beat a heavy
  mesh. Decimate or lower subsurf if the triangle count climbs (< 400000).

## Hard-surface vs organic

- Organic (creatures, characters, terrain): primitive + remesh, SUBSURF for smoothness,
  SKIN for limbs, metaballs for fused mass, displace/noise for surface.
- Hard-surface (mechanical, robots, vehicles, props): box-model panels, cut with BOOLEAN,
  and ALWAYS BEVEL the edges; add ARRAY-ed greebles for detail; keep flats truly flat.
  A boolean cut with a beveled edge reads as machined; a raw boolean reads as CG.

## Checklist

- Each part uses the paradigm that suits it (remesh organic, box-model hard-surface,
  modifiers for repetition/thickness).
- Edges that should not be razor-sharp are beveled.
- Density is even and appropriate; triangle count is under control (< 400000).
- Symmetry via MIRROR; repetition via ARRAY; smoothing via SUBSURF.
