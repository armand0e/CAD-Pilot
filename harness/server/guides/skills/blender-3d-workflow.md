# The 3D modelling workflow (do it in this order)

A good 3D model is not carved in one pass; it is built in stages, each locking in a
decision so the next stage has something solid to stand on. Skipping stages - jumping to
detail before the proportions are right, or to texture before the form reads - is why a
model ends up wrong. BUILD AFTER EACH STAGE. Do not write the whole model in one long script and build once at
the end - cad_build the blockout, look at the render, fix the proportions, then add the
next stage and build again. A rough model you can see and correct beats a perfect script
you never verified. Three or four small builds get a far better result than one big one.

Follow the order. This skill is the process; the depth for each stage is in its own skill:
modeling-techniques.md (how to build the mesh), texturing-and-materials.md (materials and
texture), lighting-and-rendering.md (presentation), animation-principles.md (motion),
blender-modeling.md (the engine tooling) and figures-and-characters.md (character craft).

## 0. Plan the model before you touch geometry

State, in words, what you are making: the subject, the STYLE (realistic, stylised,
chibi, low-poly), the real size in mm, the intended print/visual outcome, and the two or
three features it must be recognised by. Decide proportions as ratios (head : body,
eye : face). If the user gave a reference image, open it with view_image and measure the
proportions off it. A wrong plan makes every later stage wrong.

## 1. Blockout - the big shapes and proportions first

Place rough primitives for the MAJOR masses only: head, torso, limbs, or body + base.
No detail. The blockout exists to get proportion, scale, stance and silhouette right -
nothing else. Render it and check the silhouette reads before going on. Most bad models
are bad here and never recover. Get the ratios right now; it is cheap to fix a sphere's
size, expensive to fix a detailed head.

```python
import bpy
# a stylised character blockout: big head, small body (chibi ~ 1:1.5 head:body)
bpy.ops.mesh.primitive_uv_sphere_add(radius=22, location=(0, 0, 78)); head = bpy.context.active_object
bpy.ops.mesh.primitive_uv_sphere_add(radius=18, location=(0, 0, 42)); torso = bpy.context.active_object
torso.scale = (1.0, 0.8, 1.5)   # torso taller than wide
# ... limbs as capsules/cylinders. Stop. Render. Check proportion before any detail.
```

## 2. Primary forms - refine the main masses

Now shape each major mass properly: a smoothed head (SUBSURF), a tapered torso, limbs
with joints. Use MIRROR for anything symmetric so both sides stay identical. Still no
small features - just get the primary forms and their joins clean and flowing.

## 3. One cohesive form - join and remesh

A character is one form, not a pile of parts. Join the primary masses and VOXEL REMESH
into one clean watertight surface (see blender-modeling.md) - this is what makes it feel
sculpted rather than assembled, and makes it printable. Choose voxel_size for smoothness
vs triangle count. For a purely visual piece you may keep parts overlapping instead, but
one remeshed form almost always reads better.

## 4. Secondary forms and features - with relief

Add the features it is recognised by: face (eyes, nose, mouth as RELIEF - a raised rim
and a lens, not a flat disc; see figures-and-characters.md), hair as flowing masses or
strands, clothing as offset surfaces (SOLIDIFY a shell), accessories. Each feature
overlaps and, if it must print as one piece, is remeshed in or fused. Keep features large
enough to read at the model's real size.

## 5. Detail and cleanup

Add the smallest touches (creases, seams, small props). Then clean up: shade_smooth,
apply modifiers, confirm one watertight manifold (or accept a visual result). Keep the
triangle count sensible (< 400000). Detail last, and sparingly - a clean form with three
right details beats a noisy one.

## 6. Materials and texture - make it read

Assign material zones by region so the parts read apart: skin, hair, eyes, clothing,
each its own material with the right base colour, roughness and metallic. For surface
character use procedural textures (noise/voronoi -> colour and bump) or an image texture;
for a stylised look, flat distinct colours often read best. Apply materials AFTER any
remesh (remesh drops them); assign per-region by vertex position. See blender-modeling.md.

## 7. Rig and pose (if it moves or needs a pose)

Build an armature, skin with automatic weights, and pose the bones into the intended
stance BEFORE the final still (the still and the printable mesh are the frame-1 pose).
See blender-modeling.md.

## 8. Animate (if motion was asked for)

Keyframe a short, readable motion - a turntable (orbit the camera), a wave, an idle sway.
Use ease-in/ease-out (set keyframe interpolation to BEZIER) so it is not robotic; hold
the readable pose a moment. Keep it short (the build caps ~120 frames).

## 9. Light, camera and render

Frame the camera on the defining view (usually a three-quarter of the face/front). Light
with a key plus fill so form reads and shadows are not harsh. Set views = {...} to the
angles that expose the model's features and its weak spots. The build renders Cycles for
you; make the scene worth rendering.

## 10. Grade your own work, then iterate

Read the render and grade it honestly against the plan, on these axes - do not flatter it:
- Silhouette: recognisable in outline alone?
- Proportion: do the ratios match the subject/style?
- Form: do the masses flow and read as one, or look assembled?
- Features: present, correctly placed, with relief, at readable size?
- Materials: do the regions read apart; does colour/finish suit the subject?
- Presentation: good camera, lighting, and aimed views?
Name the weakest axis in one sentence, fix its CAUSE, and re-render. Two or three honest
iterations beat ten blind nudges. Stop when a stranger would name it correctly and the
weakest axis is at least acceptable.

## Checklist

- You planned style, size, proportions and key features before modelling.
- Blockout proportions were confirmed in a render before adding detail.
- Primary forms were refined and joined into one cohesive (remeshed) surface.
- Features have relief and read at real size; detail is sparing and clean.
- Material zones read apart; materials applied after remesh.
- Rig/pose and animation done only if needed, posed to the key frame.
- Camera, lighting and aimed views chosen; you graded the render on all six axes and
  fixed the weakest before finishing.
