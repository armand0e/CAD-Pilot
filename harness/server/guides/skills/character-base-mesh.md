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

## 1. Generate the base

MB-Lab is already enabled in the build. Pick a base type, then init a character:

```python
import bpy
scn = bpy.context.scene
scn.mblab_character_name = 'f_an01'   # anime female. Choose the base that fits:
#   anime female: f_an01 f_an02 f_an03   anime male: m_an01 m_an02 m_an03
#   realistic female: f_ca01 (caucasian) f_as01 (asian) f_af01 (african)
#   realistic male:   m_ca01              m_as01         m_af01
bpy.ops.mbast.init_character()        # creates the base body + face + eyes, ~14k verts, real topology
```

Now `bpy.data.objects` holds the base character mesh - a real body with a face, hands and feet in
correct proportion. This is the "base to manipulate."

## 2. Shape it BEFORE finalising

Set MB-Lab's body parameters on the scene while the character is still parametric:

```python
scn.mblab_body_mass = 0.55   # 0..1 overall body fat/mass (fuller vs lean)
scn.mblab_body_tone = 0.6    # 0..1 muscle tone/definition
```

For specific shape the request calls for - a fuller bust, wider hips, a narrower waist - the most
reliable scriptable route is to adjust the mesh region directly (MB-Lab exposes many named morphs,
but region editing is dependable): select the vertices in that area by world position and scale
them about their centre with a smooth falloff. Keep changes anatomical and symmetric.

```python
import mathutils
body = next(o for o in bpy.data.objects if o.type == 'MESH')
# example: gently enlarge the chest/bust region (tune the band to the character's height)
zmin, zmax = 1.15, 1.45   # metres, roughly nipple line on an MB-Lab figure - check the bounds
ctr = mathutils.Vector((0, 0, (zmin + zmax) / 2))
for v in body.data.vertices:
    if zmin < v.co.z < zmax and v.co.y < 0:        # front of the chest
        v.co = ctr + (v.co - ctr) * mathutils.Vector((1.0, 1.18, 1.06))
```

## 3. Finalise (applies the shape, adds the rig)

```python
bpy.ops.mbast.finalize_character()   # bakes morphs, builds the skeleton, assigns skin material
```

After this the character is a finalised mesh plus an ARMATURE you can pose.

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
