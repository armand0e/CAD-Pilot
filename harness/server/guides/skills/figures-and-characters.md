# Figures and characters

People, animals, mascots, toys, stylised objects. The goal is a readable silhouette:
someone should name it at a glance. You are not sculpting every detail; you are
composing simple primitives into the right proportions and pose.

## Work from the silhouette and proportions

Before any code, state the defining features and their proportions in words, then in
named parameters. A minion, for example: a tall rounded capsule (clearly taller than
wide, roughly 2:1), one or two big goggle eyes on the FRONT taking a large share of
the upper body, a strap across, short arms, short legs, overalls. If the ratios are
wrong the shape is not recognisable no matter how clean the geometry.

Get the big proportions right first; add small features last.

## Compose primitives, then FUSE into one body

Build the trunk, then attach each feature so it OVERLAPS and fuse everything into a
single solid. Features that merely touch or hover will float (see
connections-and-assembly.md).

```python
import FreeCAD as App
import Part

R = 15.0                    # body radius
BODY_H = 60.0               # straight section -> tall capsule (BODY_H/2R = 2:1)
cyl = Part.makeCylinder(R, BODY_H, App.Vector(0, 0, 0))
top = Part.makeSphere(R, App.Vector(0, 0, BODY_H))
bot = Part.makeSphere(R, App.Vector(0, 0, 0))
body = cyl.fuse(top).fuse(bot)

# Eye on the FRONT (-Y), sunk 1 mm into the face so it fuses (no gap!). This flat disc
# shows the connection rule; for a feature that actually READS as an eye, give it relief
# (frame ring + lens + pupil) - see "Signature features need relief" below.
face_y = -R
eye = Part.makeCylinder(7, 3, App.Vector(0, face_y + 1.0, BODY_H*0.7), App.Vector(0, -1, 0))

# Legs at the BASE, pointing down (-Z), overlapping the body
leg = Part.makeCylinder(4.5, 12, App.Vector(-7, 0, 1), App.Vector(0, 0, -1))

figure = body.fuse(eye).fuse(leg)   # ... and the rest, all fused
parts = {"Minion": figure}          # ONE solid
```

## Pose and stance

- Feet or base at the lowest Z so it stands (see orientation-and-scale.md).
- The face and defining features point toward -Y so the front render shows them.
- Symmetric features (two arms, two legs, two eyes) are mirrored across the centre.
- A wide, low base or flat feet keep it stable and printable without supports.

## Recognisability beats detail

Two big eyes placed right sell a minion far more than a perfect strap. Spend effort
on the two or three features a person names the character by. Colour is not available,
so shape must carry the identity: exaggerate the signature feature.

## Signature features need relief, not a flat disc

A single cylinder poking out of the face does NOT read as an eye: pointing at the
camera it is a flat disc that merges into the body outline, with nothing to catch
light. Because there is no colour, a feature reads only through relief - stepped
surfaces the shading can separate. Build a signature feature as concentric parts at
different depths: a raised frame RING, a distinct LENS (a dome standing proud, or a
recessed dish), and a small central FOCAL point. That reads as an eye head-on.

```python
# A minion's single goggle on the front (-Y) at eye height - reads as an eye front-on.
eye_z, face_y = BODY_H * 0.7, -R
rim   = Part.makeTorus(8, 1.8, App.Vector(0, face_y + 0.5, eye_z), App.Vector(0, 1, 0))  # raised frame ring
lens  = Part.makeSphere(7.0, App.Vector(0, face_y + 2.0, eye_z))                          # dome standing proud
pupil = Part.makeSphere(2.4, App.Vector(0, face_y - 5.5, eye_z))                          # focal point
figure = body.fuse(lens).fuse(rim).fuse(pupil)    # ... plus arms, legs, overalls, all fused
```

The same idea gives a button (ring + dome), a mouth (a recessed groove, not a painted
line), or a badge (raised border + relief inside). Verify it front-on: set a saved
view straight at the face and read it (see verify-your-work.md).

## Cute cartoon eyes that read (in Blender, with colour)

In Blender you have materials, so an eye reads through BOTH relief and colour. The rule that
makes or breaks a cute character: BIG EYES WITH A BIG DARK IRIS. The dark iris/pupil should
fill roughly half to two-thirds of the eye; a tiny pupil on a big white eye looks blank and
dead (the owl-with-pinprick-pupils look). Add a small bright catchlight and the eye comes
alive. Set the eyes on a facial plane - for an owl, a shallow facial DISC - not as bare
spheres on the forehead, and place them at or just above the middle of the face.

```python
import bpy
def cute_eye(ex, ez, face_y, R=7.0):
    # white of the eye: a sphere set into the face so mainly the front cap shows
    bpy.ops.mesh.primitive_uv_sphere_add(radius=R, location=(ex, face_y + 0.55 * R, ez))
    white = bpy.context.active_object
    # BIG dark iris: a dome ~0.62 R standing proud of the white - this is what reads as an eye
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.62 * R, location=(ex, face_y - 0.15 * R, ez))
    iris = bpy.context.active_object
    # tiny bright catchlight high on the iris - makes the eye look alive, not glassy-dead
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.14 * R,
        location=(ex - 0.22 * R, face_y - 0.55 * R, ez + 0.30 * R))
    spark = bpy.context.active_object
    return white, iris, spark
# two eyes, close and forward-facing on the face (mirror across x); iris material near-black,
# low roughness (glossy); catchlight an emissive white (Emission Strength ~3). Space centres
# about one eye-width apart so they sit close, the way big cute eyes do.
```

Two cautions from real failures: keep the iris LARGE (do not shrink it to a dot), and after
a voxel remesh assign the eye materials by vertex position - iris polys are the ones furthest
forward (most negative Y) near each eye centre. Confirm it front-on in a lit face view: if the
pupils do not clearly read as dark, they are too small or the render is too dark, not both.

## Checklist

- Proportions match the real thing (state the ratio, e.g. body 2:1) and read in front/iso.
- The defining features are present, large enough, and on the FRONT (-Y).
- Signature features have relief (frame ring + lens + focal point), not one flat disc,
  and you confirmed they read in a straight-on face view.
- Everything is fused into one solid (solid_count 1) with overlaps, nothing floating.
- It stands on its base at the lowest Z.
- You rendered front and iso and it is nameable as the requested character.
