// OpenSCAD counterparts of cad_paths (available as cad_paths.scad inside /work).
// 2D outline modules to use with linear_extrude/rotate_extrude, an SVG outline
// import that keeps millimetres, and a convex loft between two 2D children.
//   use <cad_paths.scad>;
//   linear_extrude(3) difference() { rounded_rect(60, 40, 4); translate([8, 20]) circle(d = 3.4, $fn = 48); }
//   linear_extrude(8) svg_outline("profiles/Housing.svg");      // written by create_path_body
//   loft2(0, 40) { rounded_rect(40, 30, 6, center = true); circle(d = 24, $fn = 72); }

module rounded_rect(width, height, radius = 0, center = false) {
    r = min(radius, width / 2, height / 2);
    translate(center ? [-width / 2, -height / 2] : [0, 0])
        if (r <= 0) square([width, height]);
        else hull() for (x = [r, width - r], y = [r, height - r]) translate([x, y]) circle(r = r, $fn = 48);
}

// Stadium slot; length is the overall length including the round ends.
module slot(length, width, center = false, vertical = false) {
    l = vertical ? width : length; w = vertical ? length : width;
    translate(center ? [-l / 2, -w / 2] : [0, 0])
        hull() for (x = [w / 2, l - w / 2]) translate([x, w / 2]) circle(d = w, $fn = 48);
}

// Regular polygon by circumscribed diameter or across-flats size; rotation in degrees.
module polygon_n(sides, diameter = undef, across_flats = undef, rotation = 0) {
    d = is_undef(diameter) ? across_flats / cos(180 / sides) : diameter;
    base = rotation + (!is_undef(across_flats) && sides % 2 == 0 ? 180 / sides : 0);
    rotate(base) circle(d = d, $fn = sides);
}

module hexagon(across_flats, rotation = 0) { polygon_n(6, across_flats = across_flats, rotation = rotation); }

// Circle with one flat: flat_depth is how much is cut off the diameter (D-shaft holes).
module d_shape(diameter, flat_depth, rotation = 0) {
    rotate(rotation) intersection() {
        circle(d = diameter, $fn = 96);
        translate([-diameter, -diameter]) square([diameter + diameter / 2 - flat_depth, 2 * diameter]);
    }
}

// An SVG written by create_path_body: physical millimetres, y up, origin at the file's corner.
module svg_outline(file) { import(file, dpi = 25.4); }

// Convex loft between two 2D children placed at heights h0 and h1 (hull of thin slices).
module loft2(h0, h1) {
    hull() {
        translate([0, 0, h0]) linear_extrude(0.01) children(0);
        translate([0, 0, h1 - 0.01]) linear_extrude(0.01) children(1);
    }
}
