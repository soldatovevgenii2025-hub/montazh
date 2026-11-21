import React,{useEffect,useState} from 'react';
import {AbsoluteFill,Audio,Composition,Img,OffthreadVideo,registerRoot,staticFile,useCurrentFrame,delayRender,continueRender,cancelRender} from 'remotion';
import {Scene} from './scenes/Episode';
import data from './public/film.json';

type Word={text:string;start:number;end:number};
type Pose={name:string;rest:string;aa:string;oo:string;blink?:string;eyes:[number,number];width:number;height:number};
type Event={at:number;pose:number;reason:string};
const film:any=data;
type Mouth='rest'|'aa'|'oo';
// Optional calmer articulation, enabled only by plan.mouth: open on the loudest (stressed) vowel of each longer word, hold >= minHoldFrames.
const mouthTrack=():Mouth[]|null=>{
 const cfg=film.plan?.mouth;if(!cfg)return null;
 const fps=30,n:number=film.frames,hold:number=cfg.minHoldFrames??4,shortWord:number=cfg.shortWordSec??.18,gate:number=cfg.rmsGate??.06;
 const track:Mouth[]=new Array(n).fill('rest');
 for(const w of film.words as Word[]){
  const dur=w.end-w.start,letters=[...w.text.toLowerCase().replace(/[^а-яё]/g,'')];if(dur<shortWord||!letters.length)continue;
  const slot=dur/letters.length,cands:{i:number;e:number;st:Mouth;mid:number}[]=[];
  letters.forEach((ch,i)=>{
   const st:Mouth|null='оуёю'.includes(ch)?'oo':'аяэе'.includes(ch)?'aa':null;if(!st)return;
   const f0=Math.floor((w.start+i*slot)*fps),f1=Math.max(f0+1,Math.ceil((w.start+(i+1)*slot)*fps));let e=0;for(let f=f0;f<f1;f++)e+=film.rms[f]??0;
   cands.push({i,e:e/(f1-f0),st,mid:(f0+f1)/2});
  });
  if(!cands.length)continue;
  const sorted=[...cands].sort((x,y)=>y.e-x.e),picks=[sorted[0]];
  if(dur>.5){const second=sorted.find(c=>Math.abs(c.i-sorted[0].i)>1&&c.e>=.7*sorted[0].e);if(second)picks.push(second);}
  for(const p of picks){const f0=Math.max(0,Math.round(p.mid-hold/2));for(let f=f0;f<Math.min(n,f0+hold);f++)if((film.rms[f]??0)>=gate)track[f]=p.st;}
 }
 // no run (open or closed) between two openings may be shorter than the hold
 for(let pass=0;pass<4;pass++){
  const runs:{s:number;e:number;v:Mouth}[]=[];let s=0;
  for(let f=1;f<=n;f++)if(f===n||track[f]!==track[s]){runs.push({s,e:f,v:track[s]});s=f;}
  let changed=false;
  for(let i=0;i<runs.length;i++){const r=runs[i];if(r.e-r.s>=hold)continue;
   if(r.v!=='rest'){for(let f=r.s;f<r.e;f++)track[f]='rest';changed=true;}
   else if(i>0&&i<runs.length-1&&runs[i-1].v!=='rest'&&runs[i+1].v!=='rest'){for(let f=r.s;f<r.e;f++)track[f]=runs[i-1].v;changed=true;}}
  if(!changed)break;
 }
 return track;
};
const mouths=mouthTrack();
const Cartoon:React.FC<{t:number}>=({t})=>{
 const rig=film.presenter,events:Event[]=film.poseEvents,event=[...events].reverse().find(e=>e.at<=t)??events[0],pose:Pose=rig.poses[event.pose];
 const word=film.words.find((w:Word)=>w.start<=t&&t<w.end),rms=film.rms[Math.floor(t*30)]??0;
 const phase=word?Math.min(1,(t-word.start)/Math.max(.02,word.end-word.start)):0;
 const letters=word?.text.toLowerCase().replace(/[^а-яёa-z]/g,'')??'',letter=letters[Math.min(letters.length-1,Math.floor(phase*letters.length))];
 const blink=(Math.floor(t*30)%151)<3,state=mouths?mouths[Math.min(mouths.length-1,Math.floor(t*30))]:rms<.06||!word||/[бпмbpm]/.test(letter)?'rest':/[оуёюoquw]/.test(letter)?'oo':'aa';
 const source=blink&&pose.blink?pose.blink:pose[state];
 const scale=rig.scale,left=rig.eyeAnchor[0]-pose.eyes[0]*scale,top=rig.eyeAnchor[1]-pose.eyes[1]*scale;
 return <Img data-presenter-pose={pose.name} data-mouth-state={state} src={staticFile(source)} style={{position:'absolute',left,top,width:pose.width*scale,height:pose.height*scale}}/>;
};
const Avatar:React.FC<{t:number}>=({t})=>{
 const row=[...film.avatarTimeline].reverse().find((x:any)=>x.at<=t)??film.avatarTimeline[0];
 if(row.hidden){if(film.platforms.includes('instagram'))throw new Error('Instagram avatar cannot disappear before the last frame');if(!row.reason)throw new Error('Hidden presenter needs a scene reason');return null;}
 return <div data-avatar-visible="true" style={{position:'absolute',left:row.x,top:row.y,width:row.width,height:row.height,overflow:'hidden',borderRadius:row.radius??0}}><OffthreadVideo muted src={staticFile('avatar.mp4')} style={{width:'100%',height:'100%',objectFit:'contain',objectPosition:'center'}}/></div>;
};
const Captions:React.FC<{t:number}>=({t})=>{
 const words:Word[]=film.words;let index=words.findIndex(w=>w.start<=t&&t<w.end);
 // optional plan.captionBridge: keep the previous caption through a word gap not longer than the bridge (seconds)
 const bridge:number|undefined=film.plan?.captionBridge;
 if(index<0&&bridge){const k=words.findIndex((w,i)=>i>0&&words[i-1].end<=t&&t<w.start&&w.start-words[i-1].end<=bridge);if(k>0)index=k-1;}
 if(index<0)return null;
 const group=film.captionGroups.find((g:any)=>g.from<=index&&index<g.to);if(!group)throw new Error('Every spoken word needs a caption group');
 const style=film.style;return <div style={{position:'absolute',left:style.captionX,top:style.captionY,width:style.captionWidth,textAlign:'center',fontFamily:style.fontFamily,fontWeight:800,fontSize:style.captionFontSize,lineHeight:1.17,color:style.text,textShadow:'0 2px 8px #000'}}>{words.slice(group.from,group.to).map((w,i)=><span key={i} style={{color:group.from+i===index?style.accent:style.text}}>{w.text}{' '}</span>)}</div>;
};
const Film:React.FC=()=>{const t=useCurrentFrame()/30;const [fontHandle]=useState(()=>delayRender('Own selected font'));useEffect(()=>{(async()=>{if(film.style.font_asset){const font=await new FontFace(film.style.fontFamily,`url(${staticFile('own-font.woff2')})`,{weight:'800'}).load();(document.fonts as any).add(font);}continueRender(fontHandle);})().catch(cancelRender);},[fontHandle]);return <AbsoluteFill style={{background:film.style.background}}><Scene t={t} words={film.words} plan={film.plan} style={film.style}/>{film.format==='cartoon'?<Cartoon t={t}/>:<Avatar t={t}/>}<Captions t={t}/><Audio src={staticFile('voice.mp3')}/></AbsoluteFill>};
const Root:React.FC=()=> <Composition id="CreatorFilm" component={Film} width={1080} height={1920} fps={30} durationInFrames={film.frames}/>;
registerRoot(Root);
