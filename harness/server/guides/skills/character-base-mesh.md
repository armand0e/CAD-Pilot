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

## 2. Shape the body with MB-Lab's parameters - NOT by pushing vertices

To make the figure fuller, leaner, curvier, more muscular - a bigger bust, wider hips, a narrower
waist - pass a `shape` dict to `mblab_base`. These drive MB-Lab's own morph engine, which reshapes
the body ANATOMICALLY and keeps the topology clean. This is the dependable way; hand-editing
`body.data.vertices` produces lumps and creases and is almost always worse.

```python
body = mblab_base('f_an01', shape={
    'Torso_BreastMass': 0.80,   # bust size   (0..1 around a 0.5 default)
    'Torso_BreastTone': 0.60,   # bust firmness / lift
    'Pelvis_GluteusMass': 0.62, # seat / hips
    'Torso_Mass': 0.45,         # overall torso fullness (lower = slimmer waist)
})
```

Useful morph keys (all 0..1, 0.5 = neutral): `Torso_BreastMass`, `Torso_BreastTone`,
`Torso_BreastPosZ` (bust height), `Pelvis_GluteusMass`, `Pelvis_GluteusTone`, `Torso_Mass`,
`Torso_Tone`, `Legs_UpperlegsMass`, `Shoulders_Size`, `Neck_Length`. Keep values anatomical
(roughly 0.2..0.85); pushing to the extremes distorts the mesh. The helper applies them before it
finalises, so the rig fits the shaped body. Only fall back to a gentle vertex nudge if a specific
morph key does not exist for what you need.

## 3. Keep ONE coordinate space - do NOT rescale the figure

This is the single most common way a character build goes wrong. The MB-Lab body comes in at a
real human scale (~1.7 blender units). LEAVE IT THERE. A render does not care about absolute size,
so there is no reason to shrink it to a "figurine".

If you scale or reparent only PART of the assembly, the rest is left behind: shrink the body but
build the hair and clothes at the original scale and you get a giant skirt and hair floating around
a tiny body, off camera - the render looks empty or broken even though the geometry "exists".

Rules that keep everything together:
- Do NOT wrap the body in a scaled parent (`root.scale = 0.08`) and do NOT use a "preserve the world
  matrix" reparent trick. Both detach the added parts from the body.
- Build hair, clothing and props at the SAME native scale as the body, positioned against the body's
  real landmarks (read them: `body.dimensions`, and vertex world positions).
- If you genuinely must rescale, scale the body and EVERY added part by the same factor, together, at
  the end - or parent them all to one empty and scale that empty once (children inherit it normally;
  do not then overwrite each child's world matrix).

## 4. The base ships wearing a swimsuit texture

MB-Lab's default skin has a swimsuit painted into the skin texture (a dark bra and shorts) - it is
paint, not geometry, so you cannot move or delete it by editing the mesh. Design around it:
- Clothing that COVERS the chest and hips hides it - the usual case, so just dress the figure fully
  there.
- Any skin you leave bare in those zones will show the swimsuit. If you want bare skin there, replace
  the skin material's base-colour texture with a flat skin tone (see texturing-and-materials.md)
  rather than trying to paint over it.

## 5. Art direction on top

The base gives you anatomy; a character needs the rest. Add these as your own geometry/materials, at
the body's native scale, and PARENT each to the body or its armature so they stay attached:
- HAIR: separate flowing masses and a few strands, parented to the head (figures-and-characters.md
  and character-anatomy.md for anime hair). Hair sells an anime figure as much as the face.
- CLOTHING: offset shells over the body (SOLIDIFY a duplicated region) or separate garment meshes,
  sitting on the body's real surface - not a scaled-down copy.
- POSE: rotate the finalised armature's pose bones for a natural stance (a slight weight shift beats
  a stiff T-pose).
- FACE/EXPRESSION and MATERIALS: MB-Lab gives eyes and skin; set hair/clothing materials, and
  adjust skin/eye colour to the character. See texturing-and-materials.md and lighting-and-rendering.md.

## 6. Light, render, grade

Frame and light it (lighting-and-rendering.md). Set `views` to a front and a three-quarter. Every
build also gets an auto-framed overview render, so you can always see the whole figure even before
you tune your own camera - check it to catch a detached or mis-scaled part early. Grade against
character-anatomy.md: proportion first, then the face, hair and materials.

## Notes and honest limits

- MB-Lab finalises to a DENSE, subdivided mesh (hundreds of thousands of faces) - that is expected
  and fine. The build keeps a dense character as a fast VISUAL result automatically; do NOT add
  subdivision (it is already smooth) and do NOT decimate to force a printable solid - that just
  slows things down and fights the pipeline. A character is a visual/render deliverable.
- You get a professional BASE and do the styling; expect a strong, correctly-proportioned, stylised
  character - not a finished film asset. That is still leagues beyond primitives.

## Checklist

- You started a human/anime figure from an MB-Lab base, not from primitives.
- You set body proportions with `mblab_base(shape={...})` morphs, not by pushing vertices.
- You kept the figure at native scale and built hair/clothing at that SAME scale, parented to the
  body - nothing floating detached or mis-sized.
- You covered the chest/hips (or replaced the skin texture) so the base swimsuit does not show.
- You added hair and clothing, posed the rig, set materials, and graded proportion and the face from
  a front and a three-quarter view (plus the auto overview).
