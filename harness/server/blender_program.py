"""Untrusted Blender source launcher, run headless in the networkless sandbox.

Blender executes this with `--background --python blender_program.py -- <model.bpy> <out.stl>`.
It runs the model's bpy script from an empty scene, then exports every mesh it produced to
one STL. That STL is validated in a separate FreeCAD process, exactly like the FreeCAD path;
this launcher is never trusted to certify geometry. One Blender unit is one millimetre.
"""
import sys

import bpy

argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
if len(argv) < 2:
    raise SystemExit('blender_program.py needs <model_script> <output_stl>')
user_script, out_stl = argv[0], argv[1]

# Start empty so the default cube, camera and light are never exported.
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

with open(user_script) as handle:
    code = compile(handle.read(), user_script, 'exec')
exec(code, {'__name__': '__main__', 'bpy': bpy})  # noqa: S102 - untrusted, contained by the sandbox

# Evaluate procedural objects (metaballs, modifiers) before converting/collecting.
bpy.context.view_layer.update()

# Convert curve/text/metaball/surface objects to mesh in one pass (converting a metaball
# invalidates other object references, so never hold a stale list across the convert).
bpy.ops.object.select_all(action='DESELECT')
convertible = [obj for obj in bpy.context.scene.objects if obj.type in ('CURVE', 'FONT', 'META', 'SURFACE')]
if convertible:
    for obj in convertible:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = convertible[0]
    try:
        bpy.ops.object.convert(target='MESH')
    except RuntimeError:
        pass

# Re-scan the scene with fresh references and collect every mesh object with geometry.
meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH' and len(obj.data.vertices)]
if not meshes:
    raise SystemExit('The model produced no mesh geometry to export. Build meshes with bpy.')

bpy.ops.object.select_all(action='DESELECT')
for obj in meshes:
    obj.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]

# Blender is +Z up like CAD; keep that frame, apply modifiers, one unit = one millimetre.
bpy.ops.wm.stl_export(filepath=out_stl, export_selected_objects=True,
                      apply_modifiers=True, global_scale=1.0, forward_axis='Y', up_axis='Z')
print('BLENDER_EXPORT_OK objects=%d' % len(meshes))

# A Cycles "beauty" render showing the model's materials and lighting, saved beside the
# STL as render.png. The mesh is already exported, so the camera/light/ground added here
# never reach the printable output. Best-effort: a render failure must not fail the build.
try:
    import os
    import mathutils
    scene = bpy.context.scene
    lo = [1e30, 1e30, 1e30]
    hi = [-1e30, -1e30, -1e30]
    for obj in meshes:
        for corner in obj.bound_box:
            world = obj.matrix_world @ mathutils.Vector(corner)
            for i in range(3):
                lo[i] = min(lo[i], world[i])
                hi[i] = max(hi[i], world[i])
    center = mathutils.Vector([(lo[i] + hi[i]) / 2 for i in range(3)])
    span = max((hi[i] - lo[i]) for i in range(3)) or 10.0

    if scene.camera is None:
        camera = next((o for o in scene.objects if o.type == 'CAMERA'), None)
        if camera is None:
            data = bpy.data.cameras.new('StudioCam')
            data.lens = 55
            camera = bpy.data.objects.new('StudioCam', data)
            scene.collection.objects.link(camera)
            reach = span * 2.0
            camera.location = center + mathutils.Vector((reach * 0.8, -reach, reach * 0.55))
            camera.rotation_euler = (center - camera.location).to_track_quat('-Z', 'Y').to_euler()
        scene.camera = camera
    if not any(o.type == 'LIGHT' for o in scene.objects):
        sun = bpy.data.lights.new('StudioSun', 'SUN')
        sun.energy = 4.0
        obj = bpy.data.objects.new('StudioSun', sun)
        scene.collection.objects.link(obj)
        obj.rotation_euler = (0.6, 0.2, 0.9)
    for obj in meshes:
        if not obj.data.materials:
            mat = bpy.data.materials.new('StudioDefault')
            mat.use_nodes = True
            mat.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (0.72, 0.72, 0.75, 1)
            obj.data.materials.append(mat)
    bpy.ops.mesh.primitive_plane_add(size=span * 8, location=(center[0], center[1], lo[2]))
    if scene.world is None:
        scene.world = bpy.data.worlds.new('StudioWorld')
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 48
    scene.cycles.time_limit = 45  # seconds - never block a build on rendering
    scene.render.resolution_x = scene.render.resolution_y = 640
    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath = os.path.join(os.path.dirname(out_stl), 'render.png')
    bpy.ops.render.render(write_still=True)
    print('BLENDER_RENDER_OK')
except Exception as error:  # noqa: BLE001 - the STL is the deliverable; a render is a bonus
    print('BLENDER_RENDER_SKIPPED %s' % error)
