"""Untrusted Blender source launcher, run headless in the networkless sandbox.

Blender executes this with `--background --python blender_program.py -- <model.bpy> <out.stl>`.
It runs the model's bpy script from an empty scene, then exports every mesh it produced to
one STL. That STL is validated in a separate FreeCAD process, exactly like the FreeCAD path;
this launcher is never trusted to certify geometry. One Blender unit is one millimetre.
"""
import os
import re
import sys

import bpy
import mathutils

argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
if len(argv) < 2:
    raise SystemExit('blender_program.py needs <model_script> <output_stl>')
user_script, out_stl = argv[0], argv[1]

# Start empty so the default cube, camera and light are never exported.
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# Make MB-Lab available (parametric human/anime bases) so the model can build characters from a
# professional base via bpy.ops.mbast.*. Best-effort: if it is absent or fails to load, a normal
# build is unaffected.
try:
    bpy.ops.preferences.addon_enable(module='MB_Lab')
    print('MBLAB_READY')
except Exception as error:  # noqa: BLE001 - characters are optional; never block a build
    print('MBLAB_UNAVAILABLE %s' % error)

def mblab_base(character='f_an01', finalize=True):
    """Generate an MB-Lab character base and return its body mesh. One foolproof call instead of the
    raw mbast API. character: anime female f_an01/f_an02/f_an03, anime male m_an01..; realistic
    female f_ca01 (caucasian) f_as01 (asian) f_af01 (african), male m_ca01..  finalize adds the rig.
    MB-Lab is already enabled - do NOT enable it yourself."""
    scene = bpy.context.scene
    scene.mblab_character_name = character
    bpy.ops.mbast.init_character()
    if finalize:
        bpy.ops.mbast.finalize_character()
    meshes = [o for o in scene.objects if o.type == 'MESH' and (o.name.startswith('MBlab') or character in o.name)]
    return max(meshes, key=lambda o: len(o.data.vertices), default=None)

with open(user_script) as handle:
    code = compile(handle.read(), user_script, 'exec')
# Inject the character helper so model.bpy can do `body = mblab_base('f_an01')` without touching the
# raw addon API (the model otherwise mistypes the operator namespace and the module name).
user_ns = {'__name__': '__main__', 'bpy': bpy, 'mblab_base': mblab_base}
exec(code, user_ns)  # noqa: S102 - untrusted, contained by the sandbox

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

# glTF (.glb): a complete art asset carrying materials, textures, and any rig/animation, for
# downstream tools. Exported before the studio props are added, so it holds only the model.
try:
    bpy.ops.export_scene.gltf(filepath=os.path.join(os.path.dirname(out_stl), 'model.glb'),
                              export_format='GLB', use_selection=False, export_apply=True)
    print('BLENDER_GLTF_OK')
except Exception as error:  # noqa: BLE001 - the STL is the deliverable; glTF is a bonus
    print('BLENDER_GLTF_SKIPPED %s' % error)

# A Cycles "beauty" render showing the model's materials and lighting, saved beside the
# STL as render.png. The mesh is already exported, so the camera/light/ground added here
# never reach the printable output. Best-effort: a render failure must not fail the build.
out_dir = os.path.dirname(out_stl)
# Render tiers, so iterating stays fast. A `draft` build is a quick, low-sample single still
# for checking form and proportion; without it the build does the full beauty pass (hero still
# plus the saved views). `animate` opts in to the MP4 - off by default, because it is the
# slowest step and most builds are glances at the form, not the final recording.
draft = bool(user_ns.get('draft'))
animate = bool(user_ns.get('animate'))
render_ok = False
center = mathutils.Vector((0.0, 0.0, 0.0))
span = 10.0
beauty_cam = None
try:
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
    beauty_cam = scene.camera
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
    # A soft ambient floor so unlit sides never crush to pure black - a real studio always has
    # some bounce, and the model needs to SEE its whole surface to self-check. Only added when
    # the model did not author its own world, so a deliberate lighting design is left untouched.
    if scene.world is None:
        scene.world = bpy.data.worlds.new('StudioWorld')
        scene.world.use_nodes = True
        background = scene.world.node_tree.nodes.get('Background')
        if background is not None:
            background.inputs['Color'].default_value = (0.05, 0.05, 0.06, 1.0)
            background.inputs['Strength'].default_value = 0.5
    # Blender defaults to the AgX view transform, which is filmic and mutes/darkens a simple
    # studio scene into a muddy look. Khronos PBR Neutral is built for accurate, bright asset
    # previews, so use it - but respect a model that deliberately chose a different transform.
    try:
        if scene.view_settings.view_transform == 'AgX':
            scene.view_settings.view_transform = 'Khronos PBR Neutral'
    except (TypeError, AttributeError):
        pass
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 16 if draft else 48
    scene.cycles.time_limit = 12 if draft else 45  # seconds - never block a build on rendering
    scene.render.resolution_x = scene.render.resolution_y = 512 if draft else 640
    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath = os.path.join(out_dir, 'render.png')
    bpy.ops.render.render(write_still=True)
    render_ok = True
    print('BLENDER_RENDER_OK')
except Exception as error:  # noqa: BLE001 - the STL is the deliverable; a render is a bonus
    print('BLENDER_RENDER_SKIPPED %s' % error)

# Saved views: re-render the model's own `views` directions in the same lit studio, so it can
# check its finished, textured, lit work from the angles it chose - not just the grey mesh. Each
# is a quick pass (fewer samples, tight time cap) written as view-<name>.png beside the STL.
try:
    user_views = user_ns.get('views')
    if render_ok and not draft and isinstance(user_views, dict) and user_views:
        scene = bpy.context.scene
        view_data = bpy.data.cameras.new('ViewCam')
        view_data.lens = 55
        view_cam = bpy.data.objects.new('ViewCam', view_data)
        scene.collection.objects.link(view_cam)
        scene.camera = view_cam
        scene.cycles.samples = 24
        scene.cycles.time_limit = 20
        reach = span * 2.2
        for raw_name, vec in list(user_views.items())[:4]:
            try:
                direction = mathutils.Vector((float(vec[0]), float(vec[1]), float(vec[2])))
                if direction.length < 1e-6:
                    continue
                name = re.sub(r'[^a-z0-9_-]', '', str(raw_name).lower())[:24] or 'view'
                if name == 'render':
                    continue  # reserved for the main beauty still
                view_cam.location = center + direction.normalized() * reach
                view_cam.rotation_euler = (center - view_cam.location).to_track_quat('-Z', 'Y').to_euler()
                scene.render.filepath = os.path.join(out_dir, 'view-%s.png' % name)
                bpy.ops.render.render(write_still=True)
                print('BLENDER_VIEW_OK %s' % name)
            except Exception as view_error:  # noqa: BLE001 - one bad view must not lose the rest
                print('BLENDER_VIEW_SKIPPED %s %s' % (raw_name, view_error))
        scene.camera = beauty_cam  # restore for any animation pass below
except Exception as error:  # noqa: BLE001
    print('BLENDER_VIEWS_SKIPPED %s' % error)

# Recording: if the model set a multi-frame timeline (keyframes and frame_end > frame_start),
# render a bounded MP4 with the same studio. Best-effort and frame/time-capped so it never
# stalls a build; the printed mesh and the still render are unaffected.
try:
    scene = bpy.context.scene
    if scene.camera is not None and animate and not draft and scene.frame_end > scene.frame_start:
        frames = min(scene.frame_end - scene.frame_start + 1, 120)
        scene.frame_end = scene.frame_start + frames - 1
        scene.cycles.samples = 16
        scene.cycles.time_limit = 15
        scene.render.image_settings.file_format = 'FFMPEG'
        scene.render.ffmpeg.format = 'MPEG4'
        scene.render.ffmpeg.codec = 'H264'
        scene.render.filepath = os.path.join(os.path.dirname(out_stl), 'animation.mp4')
        bpy.ops.render.render(animation=True)
        print('BLENDER_ANIM_OK frames=%d' % frames)
except Exception as error:  # noqa: BLE001
    print('BLENDER_ANIM_SKIPPED %s' % error)
