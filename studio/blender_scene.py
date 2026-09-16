"""Trusted procedural demo. Blender runs this file, never model-written scene code."""
import bpy
import json
import math
from pathlib import Path
import sys
from mathutils import Vector

ROOT=Path(sys.argv[sys.argv.index("--")+1]).resolve()
PLAN=json.loads((ROOT/"plan.json").read_text(encoding="utf-8-sig"))
CONFIG=json.loads((ROOT/"render-config.json").read_text(encoding="utf-8-sig"))
FPS=24
POSE_FPS=CONFIG["pose_fps"]
PALETTES={
 "sunset":{"ground":"#F2E1C9","wall":"#ECC795","accent":"#B86646","leaf":"#708C5F","sky":"#F7EFDF"},
 "mint":{"ground":"#D9E5C7","wall":"#B8D5BD","accent":"#497F73","leaf":"#669779","sky":"#EDF4E6"},
 "ocean":{"ground":"#DCE9EB","wall":"#BCD9E4","accent":"#4F839C","leaf":"#59968C","sky":"#EFF5F7"}}
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
scene=bpy.context.scene
scene.render.engine="BLENDER_WORKBENCH"
scene.render.resolution_x=CONFIG["width"]
scene.render.resolution_y=CONFIG["height"]
scene.render.resolution_percentage=100
scene.render.image_settings.file_format="PNG"
scene.render.image_settings.color_mode="RGB"
scene.render.image_settings.compression=20
scene.render.fps=FPS
scene.frame_start=1
scene.frame_end=PLAN["duration"]*FPS
scene.render.use_sequencer=False
scene.world.color=tuple(int(PALETTES[PLAN["scenes"][0]["palette"]]["sky"][i:i+2],16)/255 for i in (1,3,5))
shade=scene.display.shading
shade.light="FLAT"
shade.color_type="MATERIAL"
shade.show_shadows=False
shade.show_cavity=True
shade.cavity_type="SCREEN"
shade.curvature_ridge_factor=.2
shade.curvature_valley_factor=.35
shade.show_object_outline=True
shade.object_outline_color=(.14,.115,.10)
shade.background_type="WORLD"
scene.display.render_aa="8"
scene.view_settings.view_transform="Standard"

def material(color):
 key="Color_"+color
 if key in bpy.data.materials:return bpy.data.materials[key]
 m=bpy.data.materials.new(key)
 m.diffuse_color=tuple(int(color[i:i+2],16)/255 for i in (1,3,5))+(1,)
 m.use_nodes=True
 bs=m.node_tree.nodes.get("Principled BSDF")
 bs.inputs["Base Color"].default_value=m.diffuse_color
 bs.inputs["Roughness"].default_value=.82
 return m

def finish(obj,name,loc,color,parent=None):
 obj.name=name
 if parent:obj.parent=parent
 obj.location=loc
 if color:obj.data.materials.append(material(color))
 return obj

def empty(name,loc=(0,0,0),parent=None):
 obj=bpy.data.objects.new(name,None)
 bpy.context.collection.objects.link(obj)
 obj.empty_display_size=.12
 return finish(obj,name,loc,None,parent)

def ball(name,loc,scale,color,parent=None,segments=16):
 bpy.ops.mesh.primitive_uv_sphere_add(segments=segments,ring_count=10)
 obj=finish(bpy.context.object,name,loc,color,parent)
 obj.scale=scale
 for face in obj.data.polygons:face.use_smooth=True
 return obj

def box(name,loc,scale,color,parent=None,bevel=.04):
 bpy.ops.mesh.primitive_cube_add(size=1)
 obj=finish(bpy.context.object,name,loc,color,parent)
 obj.scale=scale
 bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
 if bevel:
  mod=obj.modifiers.new("Soft corners","BEVEL");mod.width=bevel;mod.segments=2
 return obj

def cone(name,loc,radius1,radius2,depth,color,parent=None,vertices=20):
 bpy.ops.mesh.primitive_cone_add(vertices=vertices,radius1=radius1,radius2=radius2,depth=depth)
 return finish(bpy.context.object,name,loc,color,parent)

def line(name,points,width,color,parent=None):
 cu=bpy.data.curves.new(name,"CURVE");cu.dimensions="3D";cu.bevel_depth=width;cu.bevel_resolution=2
 spline=cu.splines.new("POLY");spline.points.add(len(points)-1)
 for p,xyz in zip(spline.points,points):p.co=(*xyz,1)
 obj=bpy.data.objects.new(name,cu);bpy.context.collection.objects.link(obj)
 return finish(obj,name,(0,0,0),color,parent)

def plant(parent,loc,scale=1):
 x,y,z=loc
 cone("Terracotta planter",(x,y,z+.16*scale),.15*scale,.22*scale,.32*scale,"#B97050",parent)
 cone("Soil",(x,y,z+.325*scale),.19*scale,.19*scale,.016*scale,"#735444",parent)
 line("Plant stem",[(x,y,z+.3*scale),(x+.01,y,z+.9*scale)],.016*scale,"#698251",parent)
 for j in range(5):
  a=j*2.4
  leaf=ball("Olive leaf",(x+math.cos(a)*.12*scale,y+math.sin(a)*.12*scale,z+(.5+j*.075)*scale),(.08*scale,.055*scale,.18*scale),"#6F9169",parent,12)
  leaf.rotation_euler=(math.sin(a)*.8,math.cos(a)*.8,a)

def tree(parent,loc,palette):
 x,y,z=loc
 cone("Tree trunk",(x,y,z+1),.13,.09,2,"#967456",parent,12)
 for dx,dy,dz in [(-.3,0,1.6),(.32,.1,1.8),(0,-.12,2.15),(-.15,.24,2.3),(.4,.12,2.25)]:
  ball("Round tree canopy",(x+dx,y+dy,z+dz),(.52,.42,.57),palette["leaf"],parent,12)

def dressing(root,kind,p):
 box("Floating diorama base",(0,.1,-.2),(5.1,4.1,.35),"#F9F4E8",root,.18)
 box("Diorama surface",(0,.1,-.015),(4.95,3.95,.10),p["ground"],root,.13)
 box("Backdrop floor",(0,0,-.43),(18,18,.1),p["sky"],root,0)
 if kind=="courtyard":
  box("Courtyard back wall",(0,1.8,1.2),(5,.22,2.5),p["wall"],root,.06)
  box("Wall coping",(0,1.8,2.49),(5.12,.34,.13),"#F7E7CC",root,.03)
  box("Door",(.12,1.665,1.05),(1.02,.04,1.96),p["accent"],root,.16)
  for x in (-.2,.1,.4):box("Door panel",(x,1.638,1.02),(.035,.012,1.65),"#DEAB72",root,.006)
  ball("Door handle",(.48,1.58,1.05),(.04,.035,.04),"#EDC55D",root)
  for side in (-1,1):
   box("Window frame",(side*1.5,1.64,1.62),(.66,.08,.80),"#F9E5BD",root,.08)
   box("Window glass",(side*1.5,1.58,1.65),(.5,.025,.61),"#5A878A",root,.05)
   box("Window mullion",(side*1.5,1.55,1.65),(.045,.015,.61),"#F4DEBB",root,.006)
  plant(root,(-1.85,1.02,0),1.05);plant(root,(1.85,.90,0),.8)
  for j in range(5):box("Paving stone",(-1.9+j*.95,-1.28,.047),(.69,.43,.035),"#F9EDD8",root,.08)
 elif kind=="park":
  box("Garden path",(0,-.43,.048),(4.7,1.22,.022),"#EBDCC1",root,.2)
  tree(root,(-1.8,1.1,0),p);tree(root,(1.78,1.2,0),p)
  box("Park bench seat",(0,1.15,.57),(1.8,.55,.12),"#BF966E",root,.04)
  box("Park bench back",(0,1.41,.94),(1.8,.09,.53),"#CDA77C",root,.04)
  for x in (-.65,.65):box("Bench legs",(x,1.13,.28),(.1,.37,.52),"#65786A",root,.02)
  for x,y in [(-2,-1.2),(2,-1.2),(-1.7,.35),(1.7,.4)]:
   for k in range(3):cone("Grass sprig",(x+k*.08,y,.10),.025,0,.20,p["leaf"],root,6)
 else:
  box("Studio back panel",(0,1.8,1.35),(4.7,.15,2.8),p["wall"],root,.25)
  ball("Graphic sun",(-1.18,1.69,2.25),(.48,.035,.48),"#F3CA77",root)
  box("Backdrop color bar",(.9,1.66,1.5),(.8,.04,1.65),p["accent"],root,.25)
  plant(root,(-1.8,.75,0),1.2);plant(root,(1.9,1.25,0),.7)
 box("Front color accent",(0,-1.955,-.20),(1.0,.024,.055),p["accent"],root,.012)

def character(spec,parent,position):
 root=empty(spec["name"]+" / move",position,parent)
 body=empty(spec["name"]+" / body",(0,0,0),root)
 skin,outfit=spec["skin"],spec["outfit"]
 ink,hair="#342E2B","#443327"
 cone("Tunic",(0,0,1.07),.31,.25,.63,outfit,body)
 ball("Shoulders",(0,0,1.33),(.29,.19,.13),outfit,body)
 cone("Collar",(0,0,1.41),.115,.11,.08,"#F0E4CC",body)
 head=empty(spec["name"]+" / head",(0,0,1.78),body)
 ball("Face",(0,-.016,0),(.30,.255,.34),skin,head,24)
 for side in (-1,1):
  ball("Ear",(side*.29,-.002,-.005),(.06,.053,.095),skin,head)
  ball("Eye white",(side*.103,-.255,.028),(.067,.021,.083),"#FFF9EC",head)
  ball("Eye pupil",(side*.10,-.277,.027),(.028,.012,.053),ink,head)
  ball("Eye highlight",(side*.10-.008,-.288,.047),(.009,.006,.013),"#FFFFFF",head)
  line("Eyebrow",[(side*.103-.058,-.251,.147),(side*.103,-.261,.16),(side*.103+.05,-.253,.145)],.011,hair,head)
 ball("Nose",(0,-.273,-.057),(.041,.042,.054),skin,head)
 line("Smile",[(x,-.261+.022*(abs(x)/.084),-.134+.023*(x/.084)**2) for x in (-.084,-.056,-.028,0,.028,.056,.084)],.009,"#824B39",head)
 ball("Hair cap",(0,.022,.216),(.297,.244,.147),hair,head,24)
 for x,z in [(-.21,.19),(-.10,.24),(.02,.25),(.15,.225)]:
  tuft=ball("Hair fringe",(x,-.172,z),(.097,.078,.092),hair,head);tuft.rotation_euler[1]=-.3
 legs=[];arms=[];elbows=[]
 for side in (-1,1):
  leg=empty(spec["name"]+(" / left leg" if side<0 else " / right leg"),(side*.145,0,.76),body)
  ball("Trousers",(0,0,-.295),(.112,.112,.31),"#566168",leg)
  ball("Shoe",(0,-.055,-.66),(.134,.20,.095),ink,leg)
  box("Sole",(0,-.048,-.719),(.24,.35,.035),"#E5D8BC",leg,.025)
  legs.append(leg)
  arm=empty(spec["name"]+(" / left arm" if side<0 else " / right arm"),(side*.29,0,1.32),body)
  ball("Upper sleeve",(0,0,-.15),(.105,.11,.22),outfit,arm)
  elbow=empty(spec["name"]+" / elbow",(0,0,-.30),arm)
  ball("Forearm",(0,0,-.115),(.073,.08,.16),skin,elbow)
  ball("Hand",(0,0,-.273),(.080,.064,.095),skin,elbow)
  arms.append(arm);elbows.append(elbow)
 return {"root":root,"body":body,"head":head,"legs":legs,"arms":arms,"elbows":elbows,"base":position}

def ease(x):
 x=max(0,min(1,x));return x*x*(3-2*x)

def animate(rig,action,index,start,duration):
 base=rig["base"]
 for sample in range(duration*POSE_FPS+1):
  t=sample/POSE_FPS;frame=start+round(t*FPS);phase=2*math.pi*t
  root,body,head=rig["root"],rig["body"],rig["head"]
  root.location=base;root.rotation_euler=(0,0,0)
  body.location=(0,0,.007*math.sin(phase*.4+index));head.rotation_euler=(0,.025*math.sin(phase*.25+index),0)
  for j in range(2):
   rig["legs"][j].rotation_euler=(0,0,0)
   rig["arms"][j].rotation_euler=(0,(-1 if j else 1)*.13,0)
   rig["elbows"][j].rotation_euler=(0,0,0)
  if action=="walk":
   root.location.x=base[0]-1.05+ease(t/duration)*2.1;root.rotation_euler.z=.18
   walking=math.sin(phase*1.2+index*.6);body.location.z=.035*abs(walking)
   rig["legs"][0].rotation_euler.x=walking*.52;rig["legs"][1].rotation_euler.x=-walking*.52
   rig["arms"][0].rotation_euler.x=-walking*.4;rig["arms"][1].rotation_euler.x=walking*.4
  elif action in ("wave","greet"):
   if action=="greet":
    side=1 if index==0 else -1;root.rotation_euler.z=side*(.15+.22*ease(t/1.6))
    root.location.x=base[0]-side*.24*(1-ease(t/1.8));head.rotation_euler.z=side*.1
   raised=1 if index%2==0 else 0;sign=-1 if raised==1 else 1;lift=ease((t-.25-index*.3)/.8)
   rig["arms"][raised].rotation_euler.y=sign*(.13+lift*(2.10+.16*math.sin(phase*1.5)))
   rig["elbows"][raised].rotation_euler.x=-.15*lift
  elif action=="share":
   side=1 if index==0 else -1;root.rotation_euler.z=side*.20;reach=ease((t-.8-index*.2)/1.3)
   active=1 if index==0 else 0
   rig["arms"][active].rotation_euler.y=-side*(.13+.80*reach)
   rig["arms"][active].rotation_euler.x=-.45*reach;rig["elbows"][active].rotation_euler.x=-.20*reach
   head.rotation_euler.x=.06*reach;head.rotation_euler.z=side*.12
  for obj,prop in [(root,"location"),(root,"rotation_euler"),(body,"location"),(head,"rotation_euler")]:obj.keyframe_insert(data_path=prop,frame=frame)
  for obj in rig["legs"]+rig["arms"]+rig["elbows"]:obj.keyframe_insert(data_path="rotation_euler",frame=frame)

def shot_scene(number,shot,start):
 origin=number*30;root=empty(f"Scene {number+1}: {shot['setting']}",(origin,0,0));p=PALETTES[shot["palette"]]
 dressing(root,shot["setting"],p)
 count=len(shot["characters"]);spacing=.98 if count==3 else 1.24
 for i,person in enumerate(shot["characters"]):
  x=(i-(count-1)/2)*spacing;rig=character(person,root,(x,-.16,0));animate(rig,shot["action"],i,start,shot["duration"])
  shadow=ball("Ground contact shadow",(0,0,.045),(.34,.20,.006),p["accent"],rig["root"])
 if shot["action"]=="share":
  cone("Small sharing table",(0,-.7,.68),.52,.52,.09,"#D5AB79",root,32)
  cone("Table pedestal",(0,-.7,.34),.09,.07,.62,"#A97850",root)
  cone("Table base",(0,-.7,.05),.26,.26,.05,"#A97850",root)
  cone("Plate",(0,-.7,.738),.25,.25,.025,"#FFF1D2",root)
  for x,y in [(-.11,-.69),(.08,-.75),(.02,-.60)]:ball("Bread to share",(x,y,.79),(.09,.065,.046),"#C9894D",root)
 bpy.ops.object.camera_add(location=(origin+1.0,-8.7,4.2));camera=bpy.context.object;camera.name=f"Camera / shot {number+1}"
 camera.rotation_euler=(Vector((origin,0,1.20))-camera.location).to_track_quat('-Z','Y').to_euler()
 camera.data.type="ORTHO";camera.data.ortho_scale=5.9 if CONFIG["height"]>CONFIG["width"] else (4.5 if CONFIG["width"]>CONFIG["height"] else 5.2)
 camera.data.clip_end=250
 camera.keyframe_insert(data_path="location",frame=start);camera.location.x+=.13;camera.location.z+=.03
 camera.keyframe_insert(data_path="location",frame=start+shot["duration"]*FPS-1)
 marker=scene.timeline_markers.new(f"{number+1}. {shot['action'].title()}",frame=start);marker.camera=camera
 if number==0:scene.camera=camera
 return camera

frame=1;cameras=[]
for number,shot in enumerate(PLAN["scenes"]):
 cameras.append(shot_scene(number,shot,frame));frame+=shot["duration"]*FPS
try:
 editor=scene.sequence_editor_create();strips=editor.strips if hasattr(editor,"strips") else editor.sequences
 strip=strips.new_sound("Narration and original ambient sound",str(ROOT/"soundtrack.wav"),channel=1,frame_start=1)
 strip.sound.pack()
except Exception as error:print("AUDIO_PACK_NOTE",str(error),flush=True)
notes=bpy.data.texts.new("READ ME - Prompt Video Studio")
notes.write("Prompt Video Studio - offline demo template\n\nOriginal procedural cartoon animation.\nFixed template; custom agent productions may use different scenes/scripts.\n24 fps; exported character poses: 6 per second.\nShift+Left Arrow: beginning. Space: play.\nCamera cuts are timeline markers. Character empties hold editable animation.\nThe MP4 captions come from captions.srt and are added by FFmpeg.\nSee audio-info.json for narration and sound details.\n")
scene["Story title"]=PLAN["title"]
scene["Template limitations"]="Offline demo: limited stylized 3D animation, no lip sync."
scene["Plan JSON"]=json.dumps(PLAN,ensure_ascii=False)
scene.frame_set(1);scene.camera=cameras[0]
for screen in bpy.data.screens:
 for area in screen.areas:
  if area.type=="VIEW_3D":
   area.spaces.active.region_3d.view_perspective="CAMERA"
   area.spaces.active.shading.type="SOLID";area.spaces.active.shading.light="FLAT";area.spaces.active.shading.color_type="MATERIAL"
   area.spaces.active.overlay.show_overlays=False
bpy.context.preferences.filepaths.save_version=0
bpy.ops.wm.save_as_mainfile(filepath=str(ROOT/"project.blend"))
output=ROOT/"frames";output.mkdir(exist_ok=True);total=PLAN["duration"]*POSE_FPS
for index in range(total):
 scene.frame_set(1+index*(FPS//POSE_FPS));scene.render.filepath=str(output/f"frame_{index+1:05d}.png")
 bpy.ops.render.render(write_still=True)
 (ROOT/"render-progress.json").write_text(json.dumps({"frame":index+1,"total":total}),encoding="utf-8")
 print("STUDIO_FRAME",index+1,"OF",total,flush=True)
print("STUDIO_RENDER_COMPLETE",flush=True)
