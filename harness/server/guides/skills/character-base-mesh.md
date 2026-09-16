# Human and anime characters: start from a base mesh (MB-Lab)

For a person, an anime figure or any realistic humanoid, DO NOT build the body from primitives -
scripted spheres and capsules cannot reach believable anatomy, and remeshing them only makes a
blobby figure smoother. Start from a professional base mesh and CUSTOMISE it. The Blender engine
ships MB-Lab, a parametric human/anime generator: it produces an anatomically-correct, properly
topologised, rigged human or anime base in one call, and you do the art direction on top. This is
the difference between an amateur and a professional-looking character.

Use this for humans and anime figures. For rounded mascots and simple animals, keep to
figures-and-characters.md (primitives are fine there). character-anatomy.md explains the
proportion and anatomy you are judging against.

## 1. Generate the base - ONE call

MB-Lab is ALREADY ENABLED for you. Do NOT enable it, do NOT list addons, do NOT probe the API -
just call the injected helper `mblab_base(...)`. It inits and finalises a character and returns its
body mesh:

```python
import bpy
body = mblab_base('f_an01')   # anime female, finalised with a rig. Returns the body mesh object.
# base types: anime female f_an01 f_an02 f_an03; anime male m_an01 m_an02 m_an03;
#   realistic female f_ca01 (caucasian) f_as01 (asian) f_af01 (african); male m_ca01 m_as01 m_af01
```

That single call gives you a real body with a face, hands and feet in correct proportion, plus a
rig - the professional base to build on. (The raw operator namespace, if you ever need it, is
`bpy.ops.mbast.*` - note mbast, not "mblast" - but prefer the helper.)

## 2. Shape it (optional) - pass finalize=False first

To set body proportions before the rig is baked, generate WITHOUT finalising, set MB-Lab's scene
parameters, then finalise:

```python
body = mblab_base('f_an01', finalize=False)
bpy.context.scene.mblab_body_mass = 0.55   # 0..1 fuller vs lean
bpy.context.scene.mblab_body_tone = 0.6    # 0..1 muscle tone
bpy.ops.mbast.finalize_character()         # bakes the shape, adds the skeleton
```

For a specific request - a fuller bust, wider hips, a narrower waist - the dependable route is to
edit the mesh region directly after you have the body: select vertices in that area by world
position and scale them about their centre with a smooth falloff. Keep it anatomical and symmetric.

```python
import mathutils
zmin, zmax = 1.15, 1.45   # metres, roughly the nipple line on an MB-Lab figure - check the bounds
ctr = mathutils.Vector((0, 0, (zmin + zmax) / 2))
for v in body.data.vertices:
    if zmin < v.co.z < zmax and v.co.y < 0:        # front of the chest
        v.co = ctr + (v.co - ctr) * mathutils.Vector((1.0, 1.18, 1.06))
```

## 3. What you now have

A finalised character mesh (`body`) plus an ARMATURE you can pose. The base is done - the rest is
art direction.

## 4. Art direction on top

The base gives you anatomy; a character needs the rest. Add these as your own geometry/materials:
- HAIR: separate flowing masses and a few strands, parented to the head (figures-and-characters.md
  and character-anatomy.md for anime hair). Hair sells an anime figure as much as the face.
- CLOTHING: offset shells over the body (SOLIDIFY a duplicated region) or separate garment meshes.
- POSE: rotate the finalised armature's pose bones for a natural stance (a slight weight shift beats
  a stiff T-pose). Keep the key still and the printable pose the same frame.
- FACE/EXPRESSION and MATERIALS: MB-Lab gives eyes and skin; set hair/clothing materials, and
  adjust skin/eye colour to the character. See texturing-and-materials.md and lighting-and-rendering.md.

## 5. Light, render, grade

Frame and light it (lighting-and-rendering.md), set `views` to a front and a three-quarter, and
grade against character-anatomy.md - proportion first, then the face, hair and materials.

## Notes and honest limits

- The MB-Lab mesh is dense and detailed - great for a visual render. For a printable solid, it is a
  valid closed mesh; heavy edits can break watertightness, so check the audit and accept a visual
  result if needed (blender-modeling.md).
- You get a professional BASE and do the styling; expect a strong, correctly-proportioned, stylised
  character - not a finished film asset. That is still leagues beyond primitives.

## Checklist

- You started a human/anime figure from an MB-Lab base (init_character), not from primitives.
- You shaped proportions/body before finalising, and finalised to bake the shape and add the rig.
- You added hair and clothing as their own geometry, posed the rig, and set materials.
- You graded proportion and the face from a front and a three-quarter view.
