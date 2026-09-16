# Character anatomy: humans, anime figures and humanoids

A person is the hardest thing to model, because everyone knows instantly when the proportions
or anatomy are wrong. Rounded mascots forgive a lot (see figures-and-characters.md); a human or
anime figure does not. This skill is the proportion and anatomy layer for any humanoid. Read it
BEFORE a person, and build in Blender (model.bpy) with modeling-techniques.md and the staged
process in blender-3d-workflow.md.

Be realistic about the ceiling: a believable figure built from scripted primitives is genuinely
hard. Getting the PROPORTIONS and the major MASSES right - a mannequin that reads as a real body
in the correct ratios - is achievable and is 80% of the result. Fine surface anatomy (muscles,
faces at portrait quality) is beyond what a primitive-and-remesh script reaches; aim for a clean,
correctly-proportioned, appealing stylised figure, not a photoreal one.

## 1. Choose the proportion system FIRST (in head-heights)

The head is the unit. Decide the total height in heads and place every landmark by it. Getting
this map right, before any geometry, is the single thing that makes a figure read as a body.

- Realistic adult: 7.5-8 heads tall. Stylised/game: 5-6. Anime teen/adult: ~6-7 (a slightly
  larger head, longer legs). Chibi: 2-4 (huge head, tiny body - use figures-and-characters.md).
- Vertical landmarks, from the top, in head-units (7.5-head figure; scale to your choice):
  - 1: chin (bottom of the head).
  - ~1.3: shoulders. Shoulder WIDTH: ~2 heads (male), ~1.7 (female).
  - 2: nipple line / mid-chest.
  - 3: navel / natural waist (the narrowest point).
  - ~3.5: hips / crotch (roughly the vertical MIDDLE of the whole figure - legs are half your
    height, a near-universal error is legs too short).
  - 4: fingertips of a relaxed arm; wrist is at the crotch line.
  - 5.5: knees.
  - 7.5: soles of the feet.
- Female vs male masses: female - shoulders ~= hips or narrower, clear waist, hips/pelvis the
  widest, bust on the ribcage; male - shoulders wider than hips, straighter waist, flatter chest.

## 2. Anime figures specifically

Anime is a STYLE on top of the human structure, not a different skeleton. Keep the body
landmarks above; apply these on top:
- Proportion: ~6-7 heads, slim, long legs, small hands and feet, a slightly larger and rounder
  head. Female figures often exaggerate the hourglass (narrow waist, fuller hips and bust).
- Face (the make-or-break): a rounded skull, a small pointed chin, a fairly flat face. EYES ARE
  HUGE and sit AT OR BELOW the horizontal midline of the head (not high up), about one eye-width
  apart, angled slightly up at the outer corner. The nose is a tiny suggestion (a small bump or
  just a shadow), the mouth is small and low. Build eyes with real relief and a big iris plus a
  catchlight (figures-and-characters.md) - flat discs kill it.
- Hair is a defining, SEPARATE mass, not painted on: a rounded volume larger than the skull, with
  a parted fringe/bangs over the forehead and flowing locks. Block it as a few big overlapping
  chunks (a helmet-like cap + side locks + back mass), then split the front edge into a few
  pointed strands. Hair sells an anime character as much as the face.

## 3. Build the body as MASSES, then join

Model the figure as a set of simple volumes in the right places and sizes, then MIRROR, join and
remesh into one form (blender-modeling.md). Think mannequin, not muscles:
- Head: a slightly egg-shaped sphere. Neck: a short cylinder, narrower than the head, tilted very
  slightly forward.
- Ribcage/chest: an egg or rounded box, the upper torso's big mass, widest at the shoulders and
  tapering down toward the waist.
- Waist: the narrow bridge between ribcage and pelvis - do not skip it; the taper in and back out
  is what makes a torso read as a torso.
- Pelvis/hips: a rounded wedge, wider than the waist (much wider for a female figure).
- Bust (female figures): two spheres sitting ON the front of the ribcage, placed at the nipple
  line (~2 heads down), angled outward and slightly down, sized to the character, and BLENDED into
  the chest with the remesh so they read as part of the body, not stuck-on balls. This is ordinary
  figure anatomy - treat it like any other mass: correct placement, size and a smooth blend.
- Shoulders: spheres bridging ribcage and arms (the deltoids), giving the shoulders their round.
- Arms: upper arm and forearm as tapered capsules with a sphere at the elbow; forearm tapers to a
  narrower wrist. Arm length: fingertips reach mid-thigh.
- Hands: at this scale, a flattened box for the palm with a smaller mitten mass, or simple stubby
  fingers - do not attempt detailed fingers from primitives; a clean mitten reads better than bad
  fingers.
- Legs: thigh and calf as tapered capsules with a sphere at the knee; the thigh is the thickest
  limb, tapering to the ankle. Feet: wedge shapes, longer than they are wide.

```python
import bpy, math
# A proportioned female-figure BLOCKOUT in head-units. H = one head height (mm). Mirror across x.
H = 22.0
def add(kind, loc, scale, r=1.0):
    if kind == 'sphere': bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=loc)
    else: bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=1.0, location=loc)
    o = bpy.context.active_object; o.scale = scale; return o
# total height ~7 heads; feet at z=0, head centre at z=6.5H
head   = add('sphere', (0, 0, 6.5*H), (0.9, 0.95, 1.05), r=0.5*H)
neck   = add('cylinder', (0, 0, 5.95*H), (0.28*H, 0.28*H, 0.5*H))
chest  = add('sphere', (0, 0, 5.2*H), (1.05, 0.75, 1.1), r=0.62*H)   # ribcage
waist  = add('sphere', (0, 0, 4.35*H), (0.8, 0.6, 0.7), r=0.55*H)     # narrows in
pelvis = add('sphere', (0, 0, 3.6*H), (1.15, 0.8, 0.8), r=0.6*H)      # hips widest
for s in (1, -1):                                                    # mirrored pairs
    add('sphere', (s*0.5*H, -0.45*H, 5.1*H), (0.42, 0.42, 0.42), r=0.5*H)   # bust on the ribcage
    add('sphere', (s*0.95*H, 0, 5.55*H), (0.4, 0.4, 0.4), r=0.5*H)          # shoulder
    add('cylinder', (s*1.0*H, 0, 4.7*H), (0.2*H, 0.2*H, 1.3*H))             # upper arm
    add('cylinder', (s*0.55*H, 0, 2.0*H), (0.28*H, 0.28*H, 1.7*H))          # thigh
    add('cylinder', (s*0.5*H, 0, 0.6*H), (0.22*H, 0.22*H, 1.4*H))           # calf
# Then: join all, VOXEL REMESH into one smooth body, shade_smooth. Check FRONT and SIDE before
# adding the face, hair and clothing. Fix proportions here - it is cheap now, expensive later.
```

## 4. The discipline that makes it work

- Blockout the whole figure in proportion FIRST and render FRONT and SIDE (set views for both).
  Judge only the proportion map - heads tall, leg length, waist, shoulder/hip width. Fix it here.
- MIRROR everything symmetric so both halves match exactly; pose after the symmetric blockout.
- Join and VOXEL REMESH the masses into one continuous body so joints and the bust blend in; a
  pile of separate primitives never reads as a figure.
- Only then add the face (relief eyes, tiny nose/mouth), hair as its own masses, hands/feet, and
  clothing as offset shells (SOLIDIFY). Keep clothing as simple forms over the body.
- Contrapposto: even a standing figure looks alive with a slight weight shift - drop one hip,
  tilt the shoulders the opposite way. A perfectly straight, symmetric pose reads as a mannequin.

## Grade it (proportion and anatomy first)

- Proportion: count the heads and check the landmarks - is the figure the right number of heads,
  are the legs half the height, is the waist where it should be? This is the first and biggest test.
- Masses: does the torso go ribcage -> waist -> hips, do the limbs taper and bend at real joints,
  are shoulders and hips the right relative width for the sex/style?
- Face: eyes at/below the head's midline, big with real relief, correctly spaced; small nose/mouth.
- Read: from front AND side (a figure that reads from the front is often flat or wrong in profile).
- Name the worst proportion error, fix its CAUSE in the blockout, rebuild. Do not detail a body
  whose proportions are wrong - it only makes the wrongness more finished.

## Checklist

- You chose a head-height proportion system and placed the landmarks by it before modelling.
- The blockout was confirmed in FRONT and SIDE renders before any detail.
- The body is one remeshed form with ribcage/waist/hips, tapered jointed limbs, and (for a female
  figure) a bust blended into the chest - not a pile of separate primitives.
- The face uses relief eyes at/below the midline with correct anime spacing; hair is its own mass.
- Legs are ~half the total height; nothing is the "legs too short / head too small" tell.
- You graded proportion and anatomy first, from front and side, and fixed the cause of the worst error.
