# Blender modelling (model.bpy)

Blender is the engine for organic, sculpted, character and free-form work that BREP
primitives make crude: creatures, figures with real anatomy, terrain, smooth flowing
shapes. You write a Python (bpy) script in model.bpy and cad_build it; the harness runs
Blender headless, exports the mesh, and validates it as a printable solid the same way
as the other engines. Use FreeCAD (model.py) instead for precise, parametric, tolerance
-driven parts - a bracket or gear belongs there, a frog or a dragon belongs here.

## Frame, scale, and two build outcomes

- One Blender unit = one millimetre. Build at millimetre scale (a 40 mm ball is
  radius 20). +Z is up, like CAD; put the base at the lowest Z.
- Printable vs visual: if the mesh is a watertight, manifold solid, the build is a
  normal printable revision (STEP, STL, solid audit). If it is NOT watertight (an open
  surface, a sculpt with holes, Suzanne), the build still succeeds as a VISUAL result -
  it renders and animates, but is marked not verified for printing. So sculpt and render
  freely; when the user needs to print, make it watertight (SOLIDIFY, close holes, recalc
  normals) and it becomes a printable solid.
- Aim for watertight when the goal is a physical object; accept visual when the goal is a
  render or animation. State which you are producing.
- Keep the mesh under 400000 triangles. High subdivision explodes triangle count -
  levels 2-3 is plenty; decimate a dense sculpt before finishing.

## Building blocks

```python
import bpy

# Watertight primitives (each is a closed solid):
bpy.ops.mesh.primitive_uv_sphere_add(radius=15, location=(0, 0, 15), segments=48, ring_count=24)
bpy.ops.mesh.primitive_cube_add(size=20, location=(0, 0, 10))
bpy.ops.mesh.primitive_cylinder_add(radius=6, depth=30, location=(0, 0, 15))
bpy.ops.mesh.primitive_cone_add(radius1=8, radius2=0, depth=16, location=(0, 0, 40))
bpy.ops.mesh.primitive_torus_add(major_radius=12, minor_radius=3, location=(0, 0, 20))
bpy.ops.mesh.primitive_monkey_add(size=20)   # Suzanne - a quick head/creature base
```

The active object is bpy.context.active_object; name it (obj.name = "Body") and move it
with obj.location. Each separate object becomes its own solid in the export, so a body +
a separate base are two solids (an assembly), while parts that must be one piece must be
joined or fused (below).

## Modifiers are the power - smooth, thicken, mirror, repeat

```python
obj = bpy.context.active_object

def add(kind, **props):
    m = obj.modifiers.new(kind.lower(), kind)
    for k, v in props.items(): setattr(m, k, v)
    return m

add('SUBSURF', levels=2)                 # smooth an angular base into an organic form
add('MIRROR', use_axis=(True, False, False))  # symmetry: model one half that CROSSES x=0 so the
                                              # halves merge into one solid (a half that only touches
                                              # the plane stays two solids); MIRROR merges at the seam
add('BEVEL', width=1.0, segments=2)      # soften hard edges
add('SOLIDIFY', thickness=2.0)           # give an open surface real wall thickness -> watertight
add('SKIN')                              # turn a vertex/edge skeleton into a tubular limb/creature
# Modifiers are applied automatically at export; you do not need to apply them by hand.
```

Sculpt-like smoothness comes from a low-poly base plus SUBSURF, not from sculpting by
hand. For limbs, tails and tentacles, build an edge skeleton and add SKIN then SUBSURF.

## Joining, symmetry and metaballs

- One piece from several: select the objects and bpy.ops.object.join(), or use a BOOLEAN
  modifier (operation='UNION') with another object as the target. Booleans can leave
  non-manifold edges - prefer overlapping shapes joined by a union, and check watertight.
- Symmetry: model one half and add a MIRROR modifier; guarantees a symmetric character.
- Blobby, merging organic mass (a snowman, a slime, fused muscles): metaballs blend into
  one smooth surface automatically.

```python
mb = bpy.data.metaballs.new('Blob'); obj = bpy.data.objects.new('Blob', mb)
bpy.context.collection.objects.link(obj)
for (x, y, z, r) in [(0,0,10,10), (0,0,26,7), (7,0,30,3)]:
    e = mb.elements.new(); e.co = (x, y, z); e.radius = r
# metaballs convert to one watertight mesh at export.
```

## Watertight, when you want to print (skip if the goal is only a render)

- An open surface (a plane, an unclosed extrude) is NOT a solid - give it thickness with
  SOLIDIFY, or close it, before it can print.
- Flipped/inconsistent normals read as non-manifold: after heavy editing, recalc with
  bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.normals_make_consistent(inside=False);
  bpy.ops.object.mode_set(mode='OBJECT').
- Booleans between meshes that only touch at a face or edge leave zero-thickness slivers;
  overlap the shapes so the union has real volume.
- Deleting the default cube is automatic; you start from an empty scene.

## Materials, lighting and the beauty render

Every Blender build also produces a Cycles "beauty" render (the saved view named
`render`) that shows your materials and lighting - far richer than the flat mesh views.
Materials and lights affect only that render, never the printable mesh, so use them
freely to make the result read.

```python
obj = bpy.context.active_object
mat = bpy.data.materials.new('Skin'); mat.use_nodes = True
bsdf = mat.node_tree.nodes['Principled BSDF']
bsdf.inputs['Base Color'].default_value = (0.85, 0.5, 0.4, 1)   # colour (RGBA 0..1)
bsdf.inputs['Roughness'].default_value = 0.6
bsdf.inputs['Metallic'].default_value = 0.0
obj.data.materials.append(mat)

# Optional: your own camera and lights (else a studio camera, sun and ground are added).
cam = bpy.data.objects.new('Cam', bpy.data.cameras.new('Cam')); bpy.context.collection.objects.link(cam)
cam.location = (60, -80, 50); cam.rotation_euler = (1.0, 0, 0.65); bpy.context.scene.camera = cam
key = bpy.data.objects.new('Key', bpy.data.lights.new('Key', 'AREA')); bpy.context.collection.objects.link(key)
key.location = (40, -40, 60)
```

Give different parts different materials so they read apart in the render. After the
build, read the `render` view with view_image - it is the honest picture of the result.
Every build also exports model.glb (glTF): the mesh with its materials, textures and any
rig or animation, downloadable for other tools.

## Textures - procedural or from an image

Procedural texture nodes need no files and give rich surface detail. Wire a Noise,
Voronoi, Wave or Musgrave node into Base Color (through a Color Ramp) and into a Bump
node feeding Normal for relief - rock, skin, bark, hammered metal:

```python
nt = mat.node_tree; bsdf = nt.nodes['Principled BSDF']
noise = nt.nodes.new('ShaderNodeTexNoise'); noise.inputs['Scale'].default_value = 8; noise.inputs['Detail'].default_value = 6
ramp = nt.nodes.new('ShaderNodeValToRGB'); nt.links.new(noise.outputs['Fac'], ramp.inputs['Fac'])
nt.links.new(ramp.outputs['Color'], bsdf.inputs['Base Color'])
bump = nt.nodes.new('ShaderNodeBump'); bump.inputs['Strength'].default_value = 0.3
nt.links.new(noise.outputs['Fac'], bump.inputs['Height']); nt.links.new(bump.outputs['Normal'], bsdf.inputs['Normal'])
```

An attached or research image can be a texture: it is mounted read-only at
/work/images/attachments/<id> and /work/images/research/<id> (full-res <id>.original.png).

```python
img = bpy.data.images.load('/work/images/attachments/<id>.original.png')
tex = nt.nodes.new('ShaderNodeTexImage'); tex.image = img
nt.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
```

## Rigging (armatures)

An armature poses or animates a character. Build bones, parent the mesh with automatic
weights, then pose the bones. The posed shape bakes into the exported mesh (and the rig
travels in model.glb); animate the pose bones for a walk or a wave (see recording below).

```python
import bpy
arm_data = bpy.data.armatures.new('Rig'); arm = bpy.data.objects.new('Rig', arm_data)
bpy.context.collection.objects.link(arm); bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode='EDIT')
up = arm_data.edit_bones.new('upper'); up.head = (0, 0, 0);  up.tail = (0, 0, 20)
lo = arm_data.edit_bones.new('lower'); lo.head = (0, 0, 20); lo.tail = (0, 0, 40); lo.parent = up
bpy.ops.object.mode_set(mode='OBJECT')
limb = bpy.context.scene.objects['Body']          # your watertight mesh
limb.select_set(True); arm.select_set(True); bpy.context.view_layer.objects.active = arm
bpy.ops.object.parent_set(type='ARMATURE_AUTO')   # skin the mesh to the bones
bpy.ops.object.mode_set(mode='POSE')
arm.pose.bones['lower'].rotation_mode = 'XYZ'; arm.pose.bones['lower'].rotation_euler = (0.6, 0, 0)
bpy.ops.object.mode_set(mode='OBJECT')
```

## Animation and recording (optional)

If you set a multi-frame timeline (keyframes and scene.frame_end > frame_start), the
build also records a bounded MP4 with the studio render and saves it as the animation,
shown and downloadable in the app. This is for motion the user asked to see - a turntable,
a walk cycle, a mechanism moving - not for printing (the printed mesh is the frame-1 pose).

```python
import bpy
# The animated object still has to be a watertight solid (a sphere is; Suzanne is NOT).
bpy.ops.mesh.primitive_uv_sphere_add(radius=12, location=(0, 0, 12), segments=48, ring_count=24)
obj = bpy.context.active_object
obj.rotation_euler = (0, 0, 0);       obj.keyframe_insert('rotation_euler', frame=1)   # spin
obj.rotation_euler = (0, 0, 6.283);   obj.keyframe_insert('rotation_euler', frame=48)
bpy.context.scene.frame_start = 1
bpy.context.scene.frame_end = 48
```

Keep it short (up to ~120 frames; longer is trimmed) - recording renders every frame, so
it is slow. Animate transforms, shape keys, or an armature's pose bones. The still render
and the exported mesh are the frame-1 state, so pose the important frame first. For a
turntable of the whole model, orbit the camera instead of spinning the object.

Animation and rendering do not require a watertight mesh - a sculpt or Suzanne animates as
a visual result. Only add the watertight work (SOLIDIFY, close holes) when the user also
wants to print it.

## Verify and hand off to CAD

- After cad_build, render and read the views (see verify-your-work.md); set views to a
  three-quarter and a straight-on so the form and any face read.
- For a recognisable subject, apply the figures-and-characters.md craft - proportion,
  pose, features with relief - the modelling is just cleaner here.
- To print with precise features (mounting holes, flats, tolerances), model the organic
  form in model.bpy, then in a FreeCAD model.py import the exported STL
  (import_reference then Mesh.Mesh) and add the precise features there.

## Checklist

- 1 unit = 1 mm, +Z up, base at the lowest Z, real millimetre size.
- The result is watertight and manifold (it built - the audit enforces this) and under
  400000 triangles.
- Parts that are one object are joined/unioned; separate objects are a deliberate assembly.
- Modifiers (subsurf/mirror/solidify) carry the organic quality; symmetry via MIRROR.
- You rendered aimed views and it reads as the requested subject.
