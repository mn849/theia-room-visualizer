"""Synthetic checks: render a known room with a known camera, then recover it."""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import room_geometry as rg

W, H = 4032, 3024
f35 = 26.0
f = rg.focal_px_from_f35(f35, W, H)

def make_camera(height, pitch_deg, roll_deg, yaw_deg):
    # world->cv rotation for a camera at (0,height,0)
    p, r, y = map(math.radians, (pitch_deg, roll_deg, yaw_deg))
    # three.js-style camera looking -z, then convert to cv
    def rx(a): return np.array([[1,0,0],[0,math.cos(a),-math.sin(a)],[0,math.sin(a),math.cos(a)]])
    def ry(a): return np.array([[math.cos(a),0,math.sin(a)],[0,1,0],[-math.sin(a),0,math.cos(a)]])
    def rz(a): return np.array([[math.cos(a),-math.sin(a),0],[math.sin(a),math.cos(a),0],[0,0,1]])
    cam_to_world_three = ry(y) @ rx(p) @ rz(r)
    three_to_cv = np.diag([1, -1, -1])
    world_to_cv = three_to_cv @ cam_to_world_three.T
    return world_to_cv, np.array([0, height, 0])

def project(R, C, P):
    q = R @ (P - C)
    return np.array([q[0] / q[2] * f + W / 2, q[1] / q[2] * f + H / 2])

def run(height, pitch, roll, yaw, room_w=3.6, near=-1.2, far=-4.5, width_confirmed=True, use_exif=True):
    R, C = make_camera(height, pitch, roll, yaw)
    x0, x1 = -room_w / 2 + 0.3, room_w / 2 + 0.3
    corners = [np.array([x0, 0, far]), np.array([x1, 0, far]), np.array([x1, 0, near]), np.array([x0, 0, near])]
    pts = [project(R, C, P) for P in corners]
    norm = [[p[0] / W, p[1] / H] for p in pts]
    scene = rg.estimate_scene(W, H, f35=f35 if use_exif else None, floor_points=norm, room_dims={"width_m": room_w}, width_confirmed=width_confirmed)
    return scene

for args in [(1.45, -12, 0, 0), (1.2, -20, 3, 10), (1.6, -8, -2, -15)]:
    s = run(*args)
    cam = s["camera"]
    err_h = abs(s["camera_height_m"] - args[0])
    err_p = abs(cam["pitch_deg"] - args[1])
    err_r = abs(abs(cam["roll_deg"]) - abs(args[2]))
    print(args, "height", s["camera_height_m"], "pitch", round(cam["pitch_deg"], 2), "roll", round(cam["roll_deg"], 2), s["layout_source"], s["focal_source"])
    assert err_h < 0.02 and err_p < 0.3 and err_r < 0.3, (err_h, err_p, err_r)
    fl, fr = np.array(s["floor"][0]), np.array(s["floor"][1])
    assert abs(np.linalg.norm(fr - fl) - 3.6) < 0.02

# focal from vanishing points when EXIF missing (non-frontal view needs yaw)
s = run(1.45, -15, 0, 25, use_exif=False)
print("focal from VPs", round(s["_focal"]), "true", round(f), s["focal_source"])
assert abs(s["_focal"] - f) / f < 0.03

# no width -> phone-height prior
s = run(1.45, -12, 0, 0, width_confirmed=False)
print("prior height", s["camera_height_m"], "derived width", s["room_width_m"])
assert abs(s["room_width_m"] - 3.6) < 0.05

# re-projection: camera json basis must project a floor corner back to its click
R, C = make_camera(1.3, -14, 2, 8)
s = rg.estimate_scene(W, H, f35=f35, floor_points=[[p[0]/W, p[1]/H] for p in [project(R, C, np.array(P)) for P in ([-1.5,0,-4],[1.9,0,-4],[1.9,0,-1],[-1.5,0,-1])]], room_dims={"width_m": 3.4}, width_confirmed=True)
cam = s["camera"]; B = np.array([cam["basis"]["x"], cam["basis"]["y"], cam["basis"]["z"]]).T  # columns
pos = np.array(cam["position"])
for (x, z), target in zip(s["floor"][:2], ([-1.5,0,-4],[1.9,0,-4])):
    local = B.T @ (np.array([x, 0, z]) - pos)   # three camera coords
    u = local[0] / -local[2] * f + W / 2; v = -local[1] / -local[2] * f + H / 2
    expected = project(R, C, np.array(target))
    assert np.allclose([u, v], expected, atol=1.0), ([u, v], expected)
print("reprojection ok")

# vertical vanishing point from a synthetic image of vertical lines
import cv2
R, C = make_camera(1.45, -10, 2.5, 0)
img = np.full((H // 4, W // 4, 3), 200, np.uint8)
fs = f / 4
for X in np.linspace(-3, 3, 14):
    for Z in (-3.0, -5.0):
        a = R @ (np.array([X, 0.0, Z]) - C); b = R @ (np.array([X, 2.4, Z]) - C)
        pa = (int(a[0] / a[2] * fs + W / 8), int(a[1] / a[2] * fs + H / 8)); pb = (int(b[0] / b[2] * fs + W / 8), int(b[1] / b[2] * fs + H / 8))
        cv2.line(img, pa, pb, (40, 40, 40), 2)
up, n = rg.vertical_up_from_lines(img, fs)
true_up = R @ np.array([0, 1, 0])
angle = math.degrees(math.acos(min(1, float(up @ true_up))))
print("vertical VP lines", n, "error deg", round(angle, 2))
assert angle < 1.0
s = rg.estimate_scene(W // 4, H // 4, f35=f35, rgb=img)
print("auto pitch", round(s["camera"]["pitch_deg"], 2), "roll", round(s["camera"]["roll_deg"], 2), s["layout_source"])
assert abs(s["camera"]["pitch_deg"] + 10) < 1.0
print("geometry tests passed")

# depth alignment: synthesize a disparity map for a floor + back wall scene
R, C = make_camera(1.45, -12, 0, 0)
Wd, Hd = 640, 480; fd = rg.focal_px_from_f35(f35, Wd, Hd)
s = rg.estimate_scene(Wd, Hd, f35=f35)
pose = s["_pose"]
us = np.arange(Wd) - Wd / 2; vs = np.arange(Hd) - Hd / 2
uu, vv = np.meshgrid(us, vs)
rays = np.stack([uu / fd, vv / fd, np.ones_like(uu)], -1)
den = rays @ pose["Y"]
z_floor = np.where(den < -1e-4, -pose["height"] / np.minimum(den, -1e-4), np.inf)
# back wall 4 m ahead along world -Z: plane Z_world = -4 -> cv coords
nZ = pose["Z"]; z_wall = (-4.0 - (-(nZ @ pose["foot"]))) / np.where(np.abs(rays @ nZ) > 1e-6, rays @ nZ, 1e-6)
z_true = np.where((z_wall > 0) & (z_wall < z_floor), z_wall, z_floor)
disp = 0.37 / z_true + 0.05   # unknown affine
grid = rg.align_depth(s, disp, grid_cols=80)
import base64
d = np.frombuffer(base64.b64decode(grid["depth_mm_b64"]), "<u2").reshape(grid["rows"], grid["cols"]) / 1000
import cv2 as _cv
ref = _cv.resize(z_true.astype(np.float32), (80, grid["rows"]), interpolation=_cv.INTER_AREA)
rel = np.median(np.abs(d - ref) / ref)
print("depth alignment median rel error", round(float(rel), 4))
assert rel < 0.03
print("depth alignment test passed")
