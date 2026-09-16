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
