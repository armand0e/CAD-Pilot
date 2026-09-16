# Skills: task playbooks

These files are guidance, not source. They live in /work/skills and are read-only.
Each one is a short playbook for a kind of modelling task: when it applies, how to
build it with the tools you already have, the mistakes that ruin it, and how to
check the result. Reading the matching skill before you model is the single best
way to get a correct part on the first build.

## How to use a skill

1. Read the objective in design-spec.json and decide what kind of thing this is.
2. Read the one or two skills that match from the list below (Pi read tool).
3. Model with model.py, then cad_build, then verify with the skill's checklist.

You do not need permission to read a skill and you do not have to follow it to the
letter; it encodes what has worked. When a task spans two families (a bracket that
holds a gear), read both.

## Foundations (apply to almost every task)

- skills/modeling-strategy.md - the expert method for any part: decompose,
  parameterise, choose the construction method, build in a robust order, use symmetry.
- skills/orientation-and-scale.md - which way is up, real-world size, how the render
  cameras see the part.
- skills/connections-and-assembly.md - make parts actually touch, one printable body
  vs many, and reference geometry that must NOT be exported.
- skills/tooling-powerful-techniques.md - use cad_paths, cad_inspect (measure and
  section), cad_render (isolate and section), imports and the sandbox at full power.
- skills/design-for-printing.md - orientation for strength, overhangs, tolerances and
  fits, minimum features: what makes a model succeed as a physical object.
- skills/verify-your-work.md - the self-check loop: render from the request's own
  viewpoint, compare against the reference, catch "this does not look right". Read
  before you call a task done.

## Task families

- skills/enclosures-and-cases.md - boxes, lids, walls, bosses, ports, snap fits.
- skills/brackets-and-mounts.md - plates, flanges, hole patterns, fasteners, ears,
  fan / PCB / VESA / device mounts.
- skills/figures-and-characters.md - people, animals, mascots, toys, stylised shapes;
  proportion, pose and a readable silhouette.
- skills/character-anatomy.md - the proportion and anatomy layer for HUMANS, anime figures
  and humanoids: head-height canon and landmarks, the body's masses (ribcage, waist, hips,
  jointed limbs, the bust), the anime face and hair. Read before modelling any person.
- skills/blender-modeling.md - the Blender engine (model.bpy): organic, sculpted,
  character and free-form mesh work where BREP primitives look crude, and the
  Blender-to-FreeCAD handoff for printing.
- skills/blender-3d-workflow.md - the professional step-by-step process for a proper
  3D model or character (plan, blockout, forms, remesh, features, texture, rig,
  animate, light, render, grade). Read this first for any serious Blender character.
- skills/modeling-techniques.md - how to actually build a mesh: box modelling with
  bmesh, modifiers, topology, and hard-surface vs organic. The core craft.
- skills/texturing-and-materials.md - PBR materials, material zones, procedural and
  image textures, UV unwrapping, realistic vs stylised.
- skills/lighting-and-rendering.md - three-point lighting, world and mood, camera lens
  and composition, tone mapping - presentation is half the result.
- skills/animation-principles.md - timing, easing, anticipation, follow-through, arcs;
  turntables, idles, gestures, loops.
- skills/scenes-and-dioramas.md - several objects arranged on a base: ponds, terrains,
  desks, boards; composition, placement, scale, and a scene as a multi-part assembly.
- skills/mechanisms-and-gears.md - gears, racks, shafts, bearing seats, motion.
- skills/revolves-lofts-and-sweeps.md - anything round or tapering: vases, bottles,
  cups, knobs, nozzles, ducts, handles, horns.
- skills/panels-and-flat-parts.md - flat parts from an outline: plates, gaskets,
  faceplates, gears, cams, sheet brackets.
- skills/text-and-labels.md - raised or engraved letters and numbers.
- skills/fasteners-and-threads.md - screw clearance, heat-set inserts, nut traps, and
  why you must not model helical threads.
- skills/from-reference-images.md - modelling from a photo, drawing or datasheet:
  measuring, scaling, and what counts as evidence.

A good default for a substantial task: skim modeling-strategy, read the one or two task
families that fit, and keep design-for-printing and verify-your-work in mind for the end.

## Universal checklist before you finish

- Up and base: the natural "up" is +Z and the part's base sits at the lowest Z.
- Size: overall bounds match real millimetres, not an arbitrary guess.
- Connected: parts that should be one piece actually overlap, not merely touch or
  hover near each other (see connections-and-assembly.md).
- Exported the right thing: parts = {...} contains only what should exist. Anything
  you modelled as context (a phone, a board, a card, a fan) is reference, not output.
- Looked at it: you rendered the defining view and it reads as the thing requested.
- Spec current: design-spec.json records objective, real dimensions and open questions.
