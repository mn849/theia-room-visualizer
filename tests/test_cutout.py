import sys; import os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, cv2
from PIL import Image
import item_cutout as ic
H,W=600,800
img=np.full((H,W,3),(222,214,198),np.uint8)  # beige wall
img+=np.random.randint(0,4,img.shape,dtype=np.uint8)
# bench: back rail, spindles, seat (cream, distinct-ish), legs
cv2.rectangle(img,(200,150),(600,175),(90,60,40),-1)
for x in range(215,600,40): cv2.rectangle(img,(x,175),(x+8,330),(90,60,40),-1)
cv2.rectangle(img,(190,330),(610,380),(245,240,225),-1) # cream seat (lighter than wall)
for x in (200,590): cv2.rectangle(img,(x,380),(x+10,540),(90,60,40),-1)
rgb=img
# fake model alpha: everything solid except seat weak (0.3) and spindle gaps slightly filled 0.6
alpha=np.zeros((H,W),np.float32)
alpha[150:175,200:600]=1
for x in range(215,600,40): alpha[175:330,x:x+8]=1
alpha[175:330,215:600][alpha[175:330,215:600]==0]=0.0
alpha[330:380,190:610]=1; alpha[340:370,260:540]=0.2   # weak seat interior (enclosed hole)
for x in (200,590): alpha[380:540,x:x+10]=1
alpha[50:53,50:53]=1  # speck
a,st=ic.refine_alpha(rgb,alpha)
print(st, "seat restored:", a[355,400], "gap clear:", a[250,235], "speck:", a[51,51], "leg:", a[500,205])
# make an enclosed wall-colored gap with fake 0.6 alpha in spindle gap (simulates close/blur)
alpha2=alpha.copy(); alpha2[180:320,226:252]=0.3
a2,_=ic.refine_alpha(rgb,alpha2); print("enclosed wall gap ->", a2[250,235])
# grabcut full path
cut,rep=ic.make_cutout(Image.fromarray(rgb)); print(rep)
A=np.asarray(cut)[...,3]; print("cut size",cut.size)
assert a[355,400]==1 and a[250,235]==0 and a[51,51]==0 and a[500,205]==1 and a2[250,235]==0 and rep["ok"]; print("cutout tests passed")
