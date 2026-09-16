# Lighting, camera and rendering (presentation is half the result)

The same model looks like a prototype under flat light and like a product under good
light. The build renders Cycles for you and, if you add no camera or lights, drops in a
default studio - but for anything you care about, set up the shot yourself. The harness
uses your camera and lights when you provide them.

## Three-point lighting - the foundation

- Key: the main light, brightest, off to one side and above (~45 deg). Sets the form.
- Fill: softer, opposite the key, ~1/4 the key's power. Lifts the shadows so they read.
- Rim (back light): behind the subject, catches the edges and separates it from the
  background. This is what makes a render "pop".
Use AREA lights (soft shadows) sized to the subject; a large soft key flatters organic
form, a smaller harder key defines hard-surface.

```python
sc = bpy.context.scene
def light(name, energy, size, loc):
    d = bpy.data.lights.new(name, 'AREA'); d.energy = energy; d.size = size
    o = bpy.data.objects.new(name, d); sc.collection.objects.link(o); o.location = loc
    o.rotation_euler = (o.location.normalized() if False else (0.0, 0.0, 0.0))  # aim by hand below
    return o
key  = light('Key',  900, 30, ( 45, -45, 65))
fill = light('Fill', 220, 55, (-55, -30, 30))
rim  = light('Rim',  700, 20, (  0,  45, 55))
# point each light at the subject centre:
import mathutils
for L in (key, fill, rim):
    L.rotation_euler = (mathutils.Vector((0,0,40)) - L.location).to_track_quat('-Z','Y').to_euler()
```

## Exposure - do not render dark

The most common render failure is simply too dark: a single key, no fill, no world, and half
the model falls into black where nothing can be read. The harness adds only a faint ambient
floor when you set no world at all - it will NOT light the scene for you, so YOU must. Always
give it a fill and some world strength alongside the key. After the render comes back, look at
it: if the shadow side is crushed, the colours are muddy, or you cannot make out a feature
(dark pupils on a dark face), the scene is underlit - raise the key, raise the fill, or lift
world strength, and re-render. A cheerful subject (a toy, a cute character) wants bright, open
light, not moody contrast. Aim for a render where every part of the form is clearly visible.

## Environment and mood

The world is your ambient fill AND your background tone, and it is the setting that most
often decides whether a render reads cheerful or gloomy. A dark world (low colour, e.g.
0.05) gives a moody, dramatic look with a black-ish background - right for a villain or a
metal part, WRONG for a cute toy or a clean product, which come out dim and grim. Default
to a BRIGHT world for anything cheerful: a light warm-grey at strength ~1.0-1.5 lifts the
whole subject softly and gives a clean pale backdrop the subject pops against. Then the
key/fill/rim shape the form on top of that base.

```python
if sc.world is None: sc.world = bpy.data.worlds.new('W')
sc.world.use_nodes = True
bg = sc.world.node_tree.nodes['Background']
# bright, clean backdrop for a cute/product subject (go dark only for a deliberately moody one):
bg.inputs['Color'].default_value = (0.8, 0.82, 0.85, 1); bg.inputs['Strength'].default_value = 1.2
```

## Tone mapping - why your render may look dark

Blender's default view transform is AgX, which is filmic: it mutes and darkens a simple
studio scene into a muddy, greyed look even when your lights are fine. The build renders
with Khronos PBR Neutral instead - accurate, bright colour for an asset/figurine preview -
so you normally do not need to touch this. Only set view_transform yourself if you
deliberately want a cinematic AgX look (`sc.view_settings.view_transform = 'AgX'`), and know
it will need noticeably stronger lights to read bright. For a cheerful cartoon or a clean
product shot, leave it on the PBR Neutral default and light generously.

## Camera - lens, angle, focus

- Angle: a three-quarter view (front + one side + slightly above) reads a subject best;
  a straight front works for symmetry, a low angle makes something heroic/imposing.
- Lens: 50-85 mm for a natural look; wide (24-35) exaggerates and distorts (avoid for
  portraits); long (100+) flattens. Frame so the subject fills the frame with a little air.
- Depth of field: enable dof and set a low f-stop to throw the background soft and hold
  focus on the subject - instantly more photographic.

```python
cd = bpy.data.cameras.new('Cam'); cd.lens = 80; cd.dof.use_dof = True; cd.dof.aperture_fstop = 2.8
cam = bpy.data.objects.new('Cam', cd); sc.collection.objects.link(cam); sc.camera = cam
cam.location = (55, -110, 55)
cam.rotation_euler = (mathutils.Vector((0,0,40)) - cam.location).to_track_quat('-Z','Y').to_euler()
```

## Composition

- Fill the frame; leave a little breathing room, more in the direction the subject faces.
- Put the focal feature (the face) near a thirds line, not dead centre, unless symmetry is
  the point.
- A ground plane with contact shadow grounds the object; a gradient or dim world keeps
  focus on it.

## Also set the saved views

Beyond the beauty render, set views = {...} (blender-modeling.md) to the angles that show
the model's features and expose its flaws - a straight-on face, a profile, a back. The build
re-renders each of those in the SAME lit, textured studio (not just the grey mesh), so you
get your finished model shot from every angle you named - use them to catch a blank profile
or an unlit back. The review reads these too, so aim them honestly.

## Checklist

- Key/fill/rim set, AREA lights sized to the subject, each aimed at it.
- World tone and strength chosen for the intended mood; render checked for brightness (the
  build tone-maps with PBR Neutral, so a dark render means light it more, not a transform fix).
- Camera at a flattering angle, 50-85 mm, framed with a little air, DoF if it suits.
- A focal feature placed with composition in mind; ground/contact shadow present.
- Saved views aimed at the features and the weak spots.
