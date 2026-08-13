const $=id=>document.getElementById(id);
const APP_VERSION="2.9.1-beta.2";
const state={ratio:"16:9",controller:null,timer:null,started:0,abortReason:"",historyItems:[],versionMismatch:false,modelReady:"",modelLoadPromise:null,modelLoadName:"",modelLoadTimer:null,modelLoadStarted:0,modelLoadController:null,modelLoadId:"",modelLoadCancelled:false,imageMode:"",imageController:null,imageTimer:null,imageStarted:0,imageAbortReason:"",llamaDownloadTimer:null};
const STYLE_PROFILES={
  "Cinematic realism":"Naturalistic production design with physically accurate materials, motivated practical lighting, nuanced skin and surface texture, cinematic contrast, controlled depth of field and believable environmental interaction. Movement carries real weight and inertia; avoid synthetic gloss and generic stock-video polish.",
  "Live action":"Grounded live-action photography with authentic locations, practical light sources, natural exposure roll-off, convincing wardrobe and props, restrained color grading, human micro-expressions and realistic lens behavior. Preserve physical plausibility and documentary-level environmental detail.",
  "3D animation":"Premium feature-quality 3D animation with appealing sculpted forms, expressive facial rigs, detailed materials, soft global illumination, controlled subsurface scattering and confident animated posing. Motion uses readable anticipation, follow-through, squash and stretch without becoming weightless.",
  "Cartoon":"Bold graphic cartoon design with clean silhouettes, expressive shape language, simplified but intentional backgrounds, punchy color separation and highly readable poses. Use elastic timing, visual exaggeration and crisp comedic reactions while keeping character construction consistent.",
  "Anime":"Polished cinematic anime with precise linework, controlled cel shading, expressive eyes, dynamic perspective, atmospheric painted backgrounds, speed accents and dramatic color scripting. Use held poses punctuated by fluid bursts of action and carefully composed emotional close-ups.",
  "Illustrated":"Living editorial illustration with visible authored linework, layered pigment or brush texture, selective detail, designed negative space and sophisticated color harmony. Motion should feel like the illustration has come alive while preserving its handmade surface and graphic composition.",
  "Game cinematic":"High-end real-time game cinematic with detailed characters and environments, volumetric atmosphere, dramatic rim lighting, physically based materials, heroic composition and responsive action animation. Camera and editing feel authored for a premium narrative cutscene.",
  "Gameplay / first-person":"Immersive first-person gameplay presentation with stable player geography, responsive head and weapon motion, readable environmental navigation, game-authentic lighting and tactile interaction. Camera acceleration, recoil and impacts remain controlled enough to preserve spatial clarity.",
  "Stop motion":"Handcrafted stop-motion production with tactile puppets, miniature sets, visible fabric, clay, wood or paper texture, practical miniature lighting, shallow macro depth of field and intentionally stepped frame-by-frame movement. Include tiny puppet-settle imperfections while avoiding smooth CGI motion.",
  "Mixed live action and hand-drawn animation":"Live-action photography integrated with expressive hand-drawn marks that wrap around surfaces, cast light, react to movement and inhabit the same perspective. Preserve natural footage texture while animated lines, paint and symbols retain visible human variation.",
  "Graphic motion design":"Precision motion design with bold typography, geometric systems, controlled grids, clean masking, deliberate transitions and rhythmically choreographed shape animation. Every movement reinforces hierarchy and composition; surfaces remain crisp and production-ready.",
  "Animated poster":"A striking poster composition that evolves through restrained parallax, animated lighting, atmospheric particles, moving type and one memorable visual transformation. Maintain a strong hero layout and finish on a clean, readable key art frame.",
  "Premium product commercial":"Luxury commercial photography with immaculate product geometry, controlled studio reflections, refined material rendering, macro detail, elegant camera motion and sculpted highlight falloff. Interactions showcase function and craftsmanship without inventing labels, features or claims.",
  "Visceral cinematic horror":"Tactile cinematic horror with oppressive darkness, sickly practical light, damp and decayed surfaces, uncomfortable proximity, deep negative space and brief fragments of disturbing detail. Withhold the threat before revealing it; use imperfect handheld movement, abrupt stillness and low-frequency physical sound rather than constant spectacle.",
  "Psychological thriller":"Controlled psychological-thriller imagery with compressed space, reflections, frames within frames, symmetrical compositions that gradually destabilize, muted color contaminated by one recurring accent and slow invasive camera movement. Emphasize uncertain perception, micro-expressions, off-screen implication and subjective sound.",
  "Gothic whimsy":"Playfully macabre storybook gothic design with crooked architecture, elongated silhouettes, spindly trees, theatrical miniature-like sets, moonlit fog and handcrafted surface imperfections. Use charcoal, bone-white and faded jewel tones, angular compositions and expressive movement that balances childlike wonder with elegant unease; avoid direct imitation of any named filmmaker.",
  "Dark fairy tale":"Lush but threatening folklore imagery with ancient forests, worn storybook textures, candlelit chiaroscuro, jewel-toned shadows, weathered costumes and beautiful objects carrying subtle danger. Frame the world with mythic scale, enchanted atmosphere and a constant tension between wonder and menace.",
  "Cosmic horror":"Overwhelming cosmic-horror scale with tiny human figures, impossible geometry, ancient nonhuman structures, distorted horizons, starless voids and light behaving in physically unsettling ways. Reveal incomprehensible forms only partially; emphasize awe, insignificance and deep subsonic resonance over conventional monsters.",
  "Supernatural mystery":"Atmospheric supernatural mystery with ordinary locations disturbed by one impossible detail, cool nocturnal color, pools of practical light, drifting haze, reflective surfaces and patient observational framing. Build evidence gradually through environmental changes, reactions and suggestive off-screen sound.",
  "Neo-noir crime":"Modern neo-noir crime photography with hard directional light, deep blacks, wet streets, sodium and neon color contrast, glass reflections, smoke and morally charged close-ups. Use long lenses, oblique framing and deliberate urban camera moves with restrained, dangerous energy.",
  "Analog found footage":"Degraded consumer-video authenticity with imperfect autofocus, sensor noise, clipped highlights, rolling exposure, timestamp-era color, nervous reframing and accidental obstructions. Events must feel captured rather than staged; preserve plausible operator behavior and unsettling off-camera audio without decorative digital glitch overload.",
  "Retro science fiction":"Tactile retro-futurism built from practical miniatures, painted control panels, CRT displays, analog switches, brushed metal, colored instrument light and optimistic mid-century industrial design. Combine clean graphic shapes with visible model-making detail and period-authentic optical effects.",
  "Dystopian future":"Severe dystopian worldbuilding with monumental surveillance architecture, dense infrastructure, polluted atmosphere, utilitarian clothing, harsh industrial lighting and controlled institutional color. Contrast overwhelming systems with vulnerable human-scale details and credible environmental wear.",
  "Disaster spectacle":"Large-scale disaster cinema with clearly established geography, escalating structural failure, credible mass and debris physics, atmospheric depth, human reaction inserts and wide shots that communicate enormous scale. Destruction unfolds as connected cause and effect rather than random visual noise.",
  "Action blockbuster":"Premium action-blockbuster imagery with strong chase geography, bold silhouettes, dynamic parallax, practical-feeling impacts, readable stunt motion, aggressive but motivated camera placement and escalating shot scale. Maintain screen direction and physical continuity through every cut.",
  "Pulp adventure":"Colorful pulp-adventure energy with exotic practical locations, weathered maps and machinery, heroic silhouettes, golden light, dangerous terrain and bold serialized storytelling. Camera movement feels athletic and optimistic; action favors ingenious escapes and tactile set pieces.",
  "Romantic fantasy":"Luminous romantic fantasy with ethereal natural light, flowing fabric, enchanted landscapes, delicate particles, elegant production design and intimate expressive close-ups. Use graceful camera movement and rich color transitions to make emotional connection feel physically present in the environment.",
  "Surreal dreamscape":"Poetic surrealism with lucid visual logic, seamless impossible transitions, symbolic objects, altered scale, gravity-defying but graceful motion and environments that transform through visual association. Maintain coherent lighting and composition so the dream feels intentional rather than randomly generated.",
  "Stand-up comedy":"Authentic live stand-up staging with one clearly anchored comedian, microphone, stage and believable audience geography. Favor confident medium shots and close-ups for delivery, occasional wider room views and selective audience reaction cuts. Preserve natural performance gestures, pauses and facial timing; keep spoken material concise enough for the clip and avoid unnecessary cinematic action that distracts from the comedian.",
  "TV comedy":"Polished episodic television comedy with grounded locations, readable character blocking, natural ensemble performances and clean coverage that supports conversation and reaction. Let humor come from personality, situation, dry remarks, awkward timing or mild escalation rather than forcing every beat into a punchline. Use flexible medium shots, two-shots, close reactions and motivated inserts while preserving spatial continuity.",
  "Sitcom":"Grounded episodic television comedy built around familiar domestic, workplace or neighborhood situations, natural ensemble performances, clear character blocking and warm practical interiors. Let humor develop through everyday inconvenience, personality clashes, misunderstandings, dry remarks, awkward pauses and readable reaction beats rather than constant exaggeration. Favor conversational medium shots, two-shots, doorway entrances, restrained close-ups and reaction cuts that clearly establish who is speaking and listening. Stage each interaction as a simple setup, interruption or complication, response and comic payoff while keeping characters, eyelines and room geography consistent.",
  "Slapstick comedy":"Physical comedy with crystal-clear spatial geography, readable anticipation, cause and effect, exaggerated but coherent body mechanics and a visible reaction after each impact or failed attempt. Favor wider coverage for the main gag, then cut closer for contact details and reactions. Escalate physical complications without random motion, identity drift or impossible character positions unless the absurdity is explicitly part of the joke.",
  "Sketch comedy":"Short-form comedy built around a fast readable setup, escalation and decisive punchline. Use distinct visual beats, purposeful cuts, strong character reactions and concise dialogue when useful; each shot should advance the gag rather than repeat it. Preserve character identity, wardrobe, props and screen geography so the joke remains understandable even when the pacing becomes rapid.",
  "Dark comedy":"Grounded, serious-looking comedy in which irony, uncomfortable timing, inappropriate reactions or absurd consequences create the humor. Keep cinematography restrained and believable, performances controlled and reactions specific rather than cartoonish. Let the contrast between the sober presentation and the comic situation do the work; do not drift into horror merely because the subject matter is dark.",
  "Parody":"Convincingly reproduce the visual grammar, staging and dramatic conventions of the genre being spoofed, then exaggerate selected clichés for comic effect. Keep the underlying filmmaking competent enough that the parody is recognizable, use clear setups and reaction beats, and avoid random silliness that is unrelated to the target genre.",
  "Surreal comedy":"Matter-of-fact comedy built from impossible events, dream logic and absurd juxtapositions while preserving clear character identity, geography and internal continuity. Treat bizarre events as normal within the scene, use calm reactions or precise contrast to make the absurdity readable, and escalate through deliberate visual logic rather than uncontrolled randomness."
};

function init(){
  restoreSettings(); bind(); update(); checkServer(); restoreServerLlmSettings(); refreshLlamaBundleStatus(); setInterval(checkServer,15000);
}
function bind(){
  $("duration").oninput=update;$("style").onchange=update;$("soundEnabled").onchange=update;$("resolution").onchange=update;$("promptOutput").oninput=()=>{resetImagePrompt();update();};
  document.querySelectorAll("#ratioButtons button").forEach(b=>b.onclick=()=>{state.ratio=b.dataset.value;document.querySelectorAll("#ratioButtons button").forEach(x=>x.classList.toggle("active",x===b));update();});
  document.querySelectorAll("[data-example]").forEach(b=>b.onclick=()=>{$("idea").value=b.dataset.example;$("idea").focus();});
  $("referenceEnabled").onchange=()=>{$("referenceField").hidden=!$("referenceEnabled").checked;};
  $("generateButton").onclick=generate;$("regenerateButton").onclick=generate;$("cancelButton").onclick=()=>state.controller?.abort();
  $("copyButton").onclick=copyPrompt;$("downloadButton").onclick=downloadPrompt;
  $("imagePromptButton").onclick=openImagePromptDialog;$("closeImagePrompt").onclick=closeImagePromptDialog;$("cancelImagePrompt").onclick=()=>state.imageController?.abort();
  document.querySelectorAll("[data-image-mode]").forEach(button=>button.onclick=()=>selectImageMode(button.dataset.imageMode));
  $("generateImageButton").onclick=()=>generateImagePrompt(state.imageMode);
  $("regenerateImagePrompt").onclick=()=>generateImagePrompt(state.imageMode);$("copyImagePrompt").onclick=copyImagePrompt;
  $("applyReferenceGuidance").onclick=applyReferenceGuidance;
  $("settingsButton").onclick=()=>{$("settingsDialog").showModal();updateLlmBackendUI();refreshSelectedModelState();};$("closeSettings").onclick=()=>$("settingsDialog").close();$("testOllamaButton").onclick=testOllama;
  $("llmBackend").onchange=()=>{state.modelReady="";saveSettings();updateLlmBackendUI();refreshSelectedModelState();};$("browseGgufFolderButton").onclick=browseGgufFolder;$("scanGgufButton").onclick=scanGgufFolder;$("browseRunnerButton").onclick=browseLlamaRunner;const dlBtn=$("downloadLlamaBundleButton");if(dlBtn)dlBtn.onclick=startLlamaBundleDownload;const dlClose=$("closeLlamaDownload");if(dlClose)dlClose.onclick=()=>{const d=$("llamaDownloadDialog");if(d)d.close();};$("ggufFolder").onchange=saveSettings;$("llamaRunner").onchange=saveSettings;$("ggufContext").onchange=saveSettings;$("ggufModel").onchange=()=>{state.modelReady="";saveSettings();refreshSelectedModelState();};
  $("historyButton").onclick=openHistory;$("closeHistory").onclick=()=>$("historyDialog").close();$("historySearch").oninput=renderHistory;
  $("copyNetworkAddress").onclick=copyNetworkAddress;
  $("reloadCurrentVersion").onclick=()=>location.replace(`/?v=${Date.now()}`);
  $("ollamaModel").onchange=()=>{saveSettings();state.modelReady="";refreshSelectedModelState();$("ollamaMessage").textContent="Model selected but not loaded. Click Load selected model when you are ready.";$("ollamaMessage").className="settings-message";};$("ollamaUrl").onchange=()=>{saveSettings();state.modelReady="";};$("timeout").onchange=saveSettings;
  $("loadModelButton").onclick=()=>preloadSelectedModel().catch(()=>{});$("cancelModelLoadButton").onclick=cancelModelLoad;$("unloadModelButton").onclick=unloadSelectedModel;
  $("stopServerButton").onclick=stopServer;
  document.addEventListener("keydown",e=>{if((e.ctrlKey||e.metaKey)&&e.key==="Enter")generate();});
}
function update(){
  $("durationValue").textContent=`${$("duration").value} seconds`;$("durationBadge").textContent=`${$("duration").value} sec`;$("styleBadge").textContent=$("style").value;$("ratioBadge").textContent=state.ratio;$("soundBadge").textContent=$("soundEnabled").checked?"Sound on":"Silent";$("charCount").textContent=`${$("promptOutput").value.length.toLocaleString()} / 12,000`;$("imagePromptButton").hidden=!$("promptOutput").value.trim()||Boolean(state.controller);
}
function values(){return{idea:$("idea").value.trim(),style:$("style").value,camera:$("camera").value,flow:$("flow").value,duration:Number($("duration").value),ratio:state.ratio,resolution:$("resolution").value,soundEnabled:$("soundEnabled").checked,dialogue:$("dialogue").value.trim(),sound:$("sound").value.trim(),avoid:$("avoid").value.trim(),referenceEnabled:$("referenceEnabled").checked,referenceLock:$("referenceLock").value.trim()};}
function quickDraft(v){
  const camera=v.camera==="Automatic cinematic camera"?automaticCamera(v):finish(v.camera);
  const lines=[`${v.duration}-second ${v.ratio} ${v.resolution} ${v.style.toLowerCase()} video. ${styleProfile(v.style)} ${mediumSpecificRule(v)} ${continuityAnchor(v)}`];
  lines.push(...fallbackTimeline(v));
  if(v.referenceEnabled)lines.push(`REFERENCE LOCK: The supplied image defines the subject's appearance${v.referenceLock?`, including ${v.referenceLock}`:""}; preserve that exact design, proportions, colors and identity in every shot.`);
  if(v.dialogue)lines.push(`DIALOGUE LOCK: The character says exactly: ${finish(v.dialogue)} Mouth movement and timing match the words.`);
  const audio=v.soundEnabled?(v.sound?finish(v.sound):"native stereo ambience, tactile close-up effects and reactions synchronized exactly to their visible source") : "complete silence";
  lines.push(`FINAL QUALITY LOCK: ${camera} Preserve subject identity, wardrobe, environment geography, screen direction, lighting logic and story progression across every cut. Use believable weight, inertia, contact, occlusion and cause-and-effect motion. Sound treatment: ${finish(audio)} No continuity jumps, duplicated anatomy, accidental extra subjects, unreadable action or generic filler imagery.${v.avoid?` Exclude ${finish(v.avoid)}`:""}`);
  return lines.join("\n\n").slice(0,12000);
}
function expandStory(idea){
  const clean=String(idea||"").trim().replace(/[.!?]+$/g,"");
  const taste=clean.match(/^(.+?)\s+(tries|tastes|eats|drinks|licks|bites)\s+(.+?)(?:\s+for the first time)?(?:\s+and\s+(.+))?$/i);
  if(taste){
    const [,subject,verb,object,result]=taste;
    const objectPhrase=/^(?:the|a|an|his|her|their|its)\b/i.test(object)?object:`the ${object}`;
    const tasteAction=/drinks/i.test(verb)?"takes a cautious sip":/eats|bites/i.test(verb)?"takes a tentative bite":"leans in, sniffs, and takes one tentative lick";
    const biggerAction=/drinks/i.test(verb)?"a much bigger gulp":/eats|bites/i.test(verb)?"a delighted second bite":"a delighted, enthusiastic second lick";
    const payoff=result?`${subject} ${result.replace(/^gets\b/i,"suddenly gets")}`:`the unexpected taste registers all at once`;
    return `In a warm, inviting setting, ${subject} studies ${objectPhrase} with bright-eyed curiosity. After a cautious pause, ${subject} ${tasteAction}. A flicker of surprise turns into delight, prompting ${biggerAction}. Then ${payoff}. ${subject} freezes mid-motion, eyes widening and body going perfectly still as the sensation peaks, then slowly recovers with a tiny shake and gives ${objectPhrase} one deeply suspicious final look.`;
  }
  const change=clean.match(/^(.+?)\s+(turns into|transforms into|becomes)\s+(.+)$/i);
  if(change){const[,subject,verb,result]=change;return `${subject} begins in an ordinary, clearly readable moment. A subtle visual change starts at the edges and rapidly spreads across the subject as ${subject} ${verb} ${result}. The transformation builds through several distinct physical stages, reaches a striking full reveal, and ends with the transformed subject reacting to its new form.`;}
  const mishap=clean.match(/^(.+?)\s+(trips|falls|drops|spills|crashes|slips)(.*?)(?:\s+and\s+(.+))?$/i);
  if(mishap){const[,subject,verb,detail,result]=mishap;return `${subject} moves through the scene with confident purpose, unaware of the approaching problem. A small visual warning appears just before ${subject} ${verb}${detail}. The mishap unfolds in a clear chain reaction, each movement causing the next, until the full consequence is revealed. ${result?`${subject} ${result}. `:""}${subject} holds a memorable final reaction as the aftermath settles around them.`;}
  const discovery=clean.match(/^(.+?)\s+(finds|discovers|opens|sees|notices|meets)\s+(.+)$/i);
  if(discovery){const[,subject,verb,object]=discovery;return `${subject} enters with a clear goal, then pauses after noticing something unexpected. ${subject} cautiously approaches and ${verb} ${object}. Curiosity builds through small gestures and changing expressions before the discovery is fully revealed. The scene ends on ${subject}'s strongest, most readable reaction to what has just been found.`;}
  const performance=clean.match(/^(.+?)\s+(dances|sings|plays|performs|runs|chases|jumps)(.*)$/i);
  if(performance){const[,subject,verb,detail]=performance;return `${subject} prepares for a beat, focus and anticipation visible in the posture. ${subject} ${verb}${detail}, beginning simply and building in confidence, energy, and scale. The movement develops through a clear progression, reaches one visually satisfying peak, and ends on a decisive final pose and reaction.`;}
  return `The scene opens one beat before the main event, clearly establishing the subject, setting, and immediate goal. ${finish(clean)} The action unfolds as a readable chain of cause and effect: anticipation first, then the decisive action, then a strong physical and emotional reaction. Small changes in expression, posture, and movement make every beat visible, and the scene ends on a specific final pose that resolves the idea rather than cutting off mid-action.`;
}
function finish(s){if(!s)return"";return /[.!?\"'”’]$/.test(s)?s:`${s}.`;}
function styleProfile(style){return STYLE_PROFILES[style]||STYLE_PROFILES["Cinematic realism"];}
function selectedStyleLock(v){return `SELECTED VISUAL STYLE LOCK — ${v.style}: ${styleProfile(v.style)} Maintain this exact medium and art direction throughout; do not silently substitute a different visual medium or genre.`;}
function preservedPerspectiveLock(v){return isFirstPersonGameplay(v)?"FIRST-PERSON PERSPECTIVE LOCK: Keep the existing timed SHOT/BEAT structure and story exactly as written, but interpret every camera direction from the player’s eye-level viewpoint. Show the player only through consistent hands, forearms, sleeves, held equipment and restrained HUD edges; never use an external, third-person or over-the-shoulder view.":"";}
function enforceVideoStyle(v,result){
  const text=String(result||"").trim(),lock=[selectedStyleLock(v),preservedPerspectiveLock(v)].filter(Boolean).join("\n");if(!text)return text;
  return text.includes(selectedStyleLock(v))?text:`${lock}\n\n${text}`.slice(0,12000);
}
function imageStyleLock(v){
  const gameplay=isFirstPersonGameplay(v)?" This must read unmistakably as a high-end real-time 3D first-person game view, never as live-action photography, an external cinematic camera, illustration or concept art.":"";
  return `VISUAL STYLE LOCK — ${v.style}: ${styleProfile(v.style)}${gameplay} Maintain this exact medium, materials, palette and lighting language in the entire image; do not substitute another style.`;
}
function enforceImageStyle(v,result){
  const text=String(result||"").trim(),lock=imageStyleLock(v);
  return text.includes(lock)?text:`${lock} ${text}`.trim().slice(0,4000);
}
function formatGeneratedPrompt(v,result){
  let text=String(result||"").trim();if(!text)return text;
  const qualityBreak=value=>value.replace(/(^|[^\s])\s*((?:FINAL\s+)?QUALITY\s+LOCK\s*:)/gi,"$1\n\n$2").replace(/\n{3,}/g,"\n\n").trim();
  const labeled=/(?:SHOT|BEAT)\s+\d+\s*\[[^\]]+\]\s*:/i;
  if(labeled.test(text))return qualityBreak(text.replace(/[ \t\r\n]+(?=(?:SHOT|BEAT)\s+\d+\s*\[[^\]]+\]\s*:)/gi,"\n\n"));
  const ranges=[...text.matchAll(/\[((?:\d+:)?\d+(?:\.\d+)?)\s*[–—-]\s*((?:\d+:)?\d+(?:\.\d+)?)\]/g)];
  if(ranges.length!==shotCount(v))return qualityBreak(text);
  const precedingBreak=text.lastIndexOf("\n\n",ranges[0].index),blockStart=precedingBreak>=0?precedingBreak+2:0,prefix=text.slice(0,blockStart).trim(),label=effectiveFlow(v)==="One continuous shot"?"BEAT":"SHOT",shots=[];
  const cleanSegment=(segment,range)=>segment.replace(range[0],"").replace(/\s+([,.;!?])/g,"$1").replace(/^[\s,;:–—-]+/,"").trim();
  let cursor=blockStart,suffix="",timestampsLead=ranges[0].index-blockStart<=20;
  if(timestampsLead){
    const qualityMatch=text.slice(ranges.at(-1).index).match(/\s+(?=(?:FINAL\s+)?QUALITY\s+LOCK\s*:)/i),qualityStart=qualityMatch?ranges.at(-1).index+qualityMatch.index:text.length;
    ranges.forEach((range,index)=>{const end=index+1<ranges.length?ranges[index+1].index:qualityStart,content=cleanSegment(text.slice(range.index,end),range);shots.push(`${label} ${index+1} [${range[1]}–${range[2]}]: ${content}`);});
    suffix=text.slice(qualityStart).trim();
  }else{
    ranges.forEach((range,index)=>{const after=text.slice(range.index+range[0].length),ending=after.match(/[.!?](?:["”’']?)(?=\s|$)/),fallback=index+1<ranges.length?ranges[index+1].index:text.length,end=ending?range.index+range[0].length+ending.index+ending[0].length:fallback,content=cleanSegment(text.slice(cursor,end),range);shots.push(`${label} ${index+1} [${range[1]}–${range[2]}]: ${content}`);cursor=end;while(/\s/.test(text[cursor]||""))cursor++;});
    suffix=text.slice(cursor).trim();
  }
  return qualityBreak([prefix,shots.join("\n\n"),suffix].filter(Boolean).join("\n\n"));
}
function isFirstPersonGameplay(v){return /Gameplay\s*\/\s*first-person/i.test(String(v?.style||""));}
function mediumSpecificRule(v){return isFirstPersonGameplay(v)?"STRICT FIRST-PERSON GAMEPLAY LOCK: Present every moment from the player's eye-level viewpoint as a polished high-end real-time 3D game capture. The player is never shown from outside; only consistent hands, forearms, sleeves, held equipment and subtle nonverbal HUD elements may enter frame. Interpret tracking, pans and camera moves only as player locomotion, head turns, aiming or recoil. Never use third-person, over-the-shoulder, spectator, orbiting, external tracking, cinematic cutaway, live-action photography or illustrated concept-art framing.":"";}
function firstPersonGameplayValid(v,result){
  if(!isFirstPersonGameplay(v))return true;const text=String(result||""),forbidden=/(?:third[- ]person|over[- ]the[- ]shoulder|over (?:the )?(?:player|boy|girl|man|woman|character)(?:'s|s') shoulder|external camera|spectator camera|full[- ]body (?:view of )?(?:the )?player|(?:shows?|reveals?) (?:the )?player(?:'s)? (?:face|body)|camera (?:follows|tracks|orbits|circles) (?:the )?(?:player|boy|girl|man|woman|character|pair)\b)/i;if(forbidden.test(text))return false;const cues=(text.match(/first[- ]person|player(?:'s)?[- ]eye|through (?:the )?player(?:'s)? eyes|visible (?:hands|forearms)|hands? (?:enter|visible|at the lower)|forearms?|held (?:tool|weapon|equipment)|\bHUD\b|head (?:turn|movement)|player locomotion/gi)||[]).length;return cues>=2;
}
function firstPersonGameplayRepairPrompt(v,draft){return `Rewrite the complete video prompt below so it obeys strict first-person gameplay presentation while preserving its premise, every exact timestamp, story event, dialogue, sound cue and continuity detail. Output only the full corrected prompt.

Every shot must remain at the player's eye level, looking through the player's eyes. Never show the player, child or protagonist from outside. No third-person view, over-the-shoulder angle, external tracking shot, spectator camera, orbit, cutaway or full-body player view. The player may be represented only by consistent hands, forearms, sleeves, held equipment and subtle nonverbal HUD elements at the edges of frame. Convert camera tracking into player locomotion, head turns, aiming, leaning, recoil or physical reaction. Render it as polished high-end real-time 3D gameplay—not live-action footage, a movie scene or illustrated concept art. The selected camera choice is subordinate to this first-person rule.

ORIGINAL VIDEO PROMPT
${draft}`;}
function automaticCamera(v){
  const flow=effectiveFlow(v);
  if(flow==="Dynamic action sequence"||/Action blockbuster|Disaster spectacle|Pulp adventure/.test(v.style))return"Use action-motivated camera positions that alternate wide geography, low tracking, close impact detail, point of view and reaction shots. Preserve screen direction and spatial continuity while increasing speed and scale.";
  if(flow==="Fast commercial-style cuts")return"Use energetic vertical-social framing with friendly handheld two-shots, tactile demonstration inserts, whip-pans and jump cuts motivated by each handoff. Keep both presenters personable, the process readable and every spoken line synchronized to the visible speaker.";
  if(flow==="Suspense / thriller buildup"||/Psychological thriller|Neo-noir crime|Supernatural mystery/.test(v.style))return"Begin with controlled observational framing, then move progressively closer through slow push-ins, obstructed angles, reflections and subjective reaction shots as certainty erodes.";
  if(flow==="Horror escalation and reveal"||/horror|Gothic whimsy|Dark fairy tale/i.test(v.style))return"Use negative space, withheld point of view, unsettling detail inserts and reaction shots to imply the threat before a carefully staged reveal; contrast creeping movement with abrupt stillness.";
  if(flow==="Stand-up performance")return"Anchor the comedian to the stage and microphone. Favor medium and close performer coverage with occasional wider room views and selective audience reactions, preserving stage direction and allowing pauses and delivery timing to register.";
  if(flow==="Sitcom dialogue beats"||flow==="TV comedy scene beats")return"Use clean conversational coverage: readable two-shots and medium shots, doorway or entrance angles when motivated, restrained close-ups for key lines, and listener reaction cuts. Preserve eyelines, room geography and who is speaking at every moment.";
  if(flow==="Slapstick escalation")return"Keep the main physical gag readable in wider framing, then cut to impact or prop details and clear reactions. Preserve cause and effect, screen direction and body geography as the physical complication escalates.";
  if(flow==="Sketch comedy beats")return"Use concise setup, escalation and punchline coverage with clearly differentiated angles. Cut only when the gag advances, favor reaction shots at turning points and hold long enough for the final punchline to land.";
  if(flow==="Dark comedy restraint")return"Use restrained, grounded coverage with controlled compositions, dry reaction close-ups and deliberate pauses. Let the serious visual treatment contrast with the absurd or uncomfortable situation rather than signaling the joke with exaggerated camera work.";
  if(flow==="Parody genre beats")return"Adopt the camera grammar of the genre being spoofed and execute it convincingly, then heighten selected conventions for comic payoff. Preserve readable geography and reaction timing so the parody remains specific rather than random.";
  if(flow==="Surreal comedy progression")return"Frame impossible events with calm, coherent camera logic. Preserve subject identity and geography, use composed reactions and motivated reveals, and let each absurd escalation remain visually understandable.";
  return"Begin with a strong establishing composition, vary shot scale as the story develops, move closer for the decisive action and hold long enough for the final emotional reaction to land.";
}
function effectiveFlow(v){
  if(v.flow!=="Let H3 decide")return v.flow;
  const context=`${v.style} ${v.idea}`;
  if(v.style==="Stand-up comedy")return"Stand-up performance";
  if(v.style==="Sitcom")return"Sitcom dialogue beats";
  if(v.style==="TV comedy")return"TV comedy scene beats";
  if(v.style==="Slapstick comedy")return"Slapstick escalation";
  if(v.style==="Sketch comedy")return"Sketch comedy beats";
  if(v.style==="Dark comedy")return"Dark comedy restraint";
  if(v.style==="Parody")return"Parody genre beats";
  if(v.style==="Surreal comedy")return"Surreal comedy progression";
  if(/horror|monster|creature|terrifying|nightmare|demon|ghost|haunt|slimy|roar|scary|macabre/i.test(context))return"Horror escalation and reveal";
  if(/thriller|suspense|mystery|stalk|intruder|conspiracy|noir|unease|paranoi/i.test(context))return"Suspense / thriller buildup";
  if(/Action blockbuster|Disaster spectacle|Pulp adventure/.test(v.style)||/\baction\b|chase|pursu|escape|battle|mech|explod|crash|disaster|fight|race/i.test(v.idea))return"Dynamic action sequence";
  if(/commercial|product|advert|logo|hero product|motion design|animated poster|quick cuts|social media|\bugc\b|tutorial|explaining|how to/i.test(context))return"Fast commercial-style cuts";
  if(/meditat|contemplat|quiet|gentle|tender|slow|dreamscape|romantic/i.test(context))return"Slow, deliberate pacing";
  return"Multiple cinematic shots";
}
function flowText(flow){return({"Let H3 decide":"Let the model choose the most effective shot progression for clarity and impact.","One continuous shot":"Present the action as one coherent continuous shot without cuts.","Multiple cinematic shots":"Use a small number of motivated cinematic shots with smooth, readable transitions.","Fast commercial-style cuts":"Use energetic commercial-style cuts while keeping the subject and action easy to follow.","Slow, deliberate pacing":"Use slow, deliberate pacing with time for each action and expression to register.","Stand-up performance":"Keep the performer anchored and let delivery, pauses and audience reaction determine the cuts.","Sitcom dialogue beats":"Build a readable setup, interruption or complication, response and comic payoff with conversational coverage.","TV comedy scene beats":"Use flexible episodic coverage that supports grounded character interaction, dialogue and reaction.","Slapstick escalation":"Stage physical cause and effect clearly, escalating through anticipation, impact and reaction.","Sketch comedy beats":"Move quickly from setup to escalation to a decisive punchline without repeating the gag.","Dark comedy restraint":"Keep the visual treatment serious and controlled so irony and uncomfortable contrast create the humor.","Parody genre beats":"Use the target genre’s authentic visual grammar, then exaggerate selected conventions for the payoff.","Surreal comedy progression":"Escalate impossible events through coherent visual logic and matter-of-fact reactions."})[flow]||"";}
function flowProfile(flow,duration){
  const shots=duration>=13?"five to six":duration>=9?"four to five":"three to four";
  return({
    "Let H3 decide":"Choose the clearest shot progression for the idea, varying composition and scale while preserving story geography.",
    "One continuous shot":"Tell the entire story in one motivated unbroken camera move with purposeful reframing and a clear beginning, escalation and ending.",
    "Multiple cinematic shots":`Use ${shots} distinct, action-motivated shots that establish the scene, develop the action, reveal the payoff and land on a final reaction.`,
    "Dynamic action sequence":`Build ${shots} fast, clearly differentiated shots: establish geography, enter low tracking or pursuit, cut to point of view and impact detail, include a reaction or scale shot, then finish with an escalating climax. Use hard cuts motivated by movement; preserve screen direction and never repeat an angle.`,
    "Suspense / thriller buildup":`Build ${shots} progressively tighter shots. Begin with controlled distance, introduce one suspicious detail, alternate subjective point of view with restrained reactions, delay confirmation and finish on a decisive reveal or disturbing implication.`,
    "Horror escalation and reveal":`Build ${shots} escalating shots that move from normality to unease, partial evidence, threatened reaction and a final frightening reveal. Let off-screen space and sound create dread; avoid exposing the threat too early.`,
    "Stand-up performance":`Use ${shots} restrained performance beats centered on the comedian: establish the room, move into delivery coverage, allow a pause, use at most selective audience reactions, and finish on the strongest line or reaction without unnecessary scene changes.`,
    "Sitcom dialogue beats":`Build ${shots} clear sitcom beats: establish the everyday situation, introduce an interruption or complication, alternate speaker and listener coverage, land the response or reversal, and finish on a readable comic payoff or reset. Keep dialogue concise and room geography stable.`,
    "TV comedy scene beats":`Build ${shots} grounded episodic comedy beats with clear character interaction, flexible conversational coverage, motivated reactions and a light payoff. Do not force every shot into an exaggerated joke.`,
    "Slapstick escalation":`Build ${shots} physical-comedy beats with clear anticipation, cause, impact, recovery and escalating consequence. Keep the main action wide enough to understand, then use closer inserts and reactions without breaking body or screen geography.`,
    "Sketch comedy beats":`Build ${shots} short-form comedy beats that move efficiently from setup to escalation to a decisive punchline. Every cut must introduce a new action, reversal or reaction; reserve enough time for the final gag to register.`,
    "Dark comedy restraint":`Build ${shots} controlled beats in which an ordinary or serious setup develops an ironic, uncomfortable or absurd turn. Keep performance and camera restrained, use precise pauses and reactions, and finish on the contrast rather than drifting into horror.`,
    "Parody genre beats":`Build ${shots} beats using the authentic progression of the genre being spoofed, then progressively exaggerate recognizable conventions until the comic payoff. Keep the target genre visually legible throughout.`,
    "Surreal comedy progression":`Build ${shots} coherent absurdist beats: establish a normal rule, introduce one impossible event, let characters react matter-of-factly, escalate through related dream logic and finish on a clear surreal payoff rather than random imagery.`,
    "Fast commercial-style cuts":"Use crisp rhythmic cuts, macro details, graphic match transitions and a strong final hero composition while keeping every subject and action readable.",
    "Slow, deliberate pacing":"Use few patient compositions, subtle performance changes and restrained camera movement so anticipation, physical action and emotional reaction register fully."
  })[flow]||"";
}
function shotCount(v){
  if(v.flow==="One continuous shot")return v.duration>=12?5:v.duration>=8?4:3;
  if(v.duration>=13)return 6;
  if(v.duration>=9)return 5;
  return v.duration>=6?4:3;
}
function formatTime(seconds){
  const rounded=Math.round(seconds*10)/10;
  const minutes=Math.floor(rounded/60),secs=rounded-minutes*60;
  return `${minutes}:${secs.toFixed(1).padStart(4,"0")}`;
}
function timelineRanges(v){
  const count=shotCount(v),ranges=[];
  for(let i=0;i<count;i++)ranges.push([v.duration*i/count,v.duration*(i+1)/count]);
  return ranges;
}
function timelineTemplate(v){
  const label=v.flow==="One continuous shot"?"BEAT":"SHOT";
  return timelineRanges(v).map((r,i)=>`${label} ${i+1} [${formatTime(r[0])}–${formatTime(r[1])}]:`).join("\n");
}
function continuityAnchor(v){
  const reference=v.referenceEnabled?` The supplied reference image is the identity anchor${v.referenceLock?` for ${v.referenceLock}`:""}.`:"";
  return `Establish one stable subject design, wardrobe, location layout and lighting direction before the action changes.${reference} Narrative premise: ${finish(sanitizeStyleReferences(v.idea))}`;
}
function sanitizeStyleReferences(text){
  return String(text||"")
    .replace(/(?:in\s+)?(?:the\s+)?(?:style|look|aesthetic)\s+of\s+(?:David\s+)?Cronenberg/gi,"with clinical, tactile biomechanical body horror")
    .replace(/David\s+Cronenberg(?:'s)?/gi,"tactile biomechanical")
    .replace(/(?:in\s+)?(?:the\s+)?(?:style|look|aesthetic)\s+of\s+Tim\s+Burton/gi,"with playfully macabre gothic storybook traits")
    .replace(/Tim\s+Burton(?:'s)?/gi,"playfully macabre gothic");
}
function fallbackTimeline(v){
  const ranges=timelineRanges(v),continuous=v.flow==="One continuous shot",flow=effectiveFlow(v),events=storyEvents(v.idea);
  if(events.length>=3)return eventDrivenTimeline(v,ranges,events,flow,continuous);
  return eventDrivenTimeline(v,ranges,builtInStoryEvents(v),flow,continuous);
}
function builtInStoryEvents(v){
  if(isClayCafe(v.idea))return[
    "In a completely photoreal neighborhood café, a palm-sized-textured man made entirely of warm terracotta modeling clay shuffles to the register; fingerprints and tiny kneading cracks remain visible across his face while every customer, cup and surface around him is real.",
    "The young barista smiles and points to three unbranded cups lined up from small to large, asking, “Small, medium, or large?”; the clay man studies them so intensely that his brow physically creases beneath the menu light.",
    "He presses both clay hands against his own torso, squashing himself short and wide like the small cup, then stretching tall and thin like the large one; he springs back with a soft wobble and admits, “I don’t know what size I am emotionally.”",
    "Suppressing a laugh, the barista slides the medium cup between the others and says, “Medium: enough coffee, fewer consequences.”; he leans close, comparing its silhouette to his own body as his clay chin droops thoughtfully onto the counter.",
    "The clay man brightens, pinches his sagging chin back into shape and declares, “Medium. I can grow into it.”; when he taps his payment card, one soft fingertip flattens against the reader and he peels it free with an embarrassed little snap.",
    "She hands him the steaming medium coffee; heat gently softens his fingers around the cup and his worried mouth reshapes itself into a broad thumbprint smile. The barista says, “Perfect fit,” and he exits proudly as the realistic customers finally burst into affectionate laughter."
  ];
  if(isBoardroomDance(v.idea))return[
    "Inside a dark, wood-paneled 1920 boardroom, eight severe executives sit rigidly around a long table while the chairman drones over a ledger; fountain pens scratch, cigar smoke hangs beneath the lamps and a wall clock ticks with suffocating precision.",
    "One executive's pencil stops mid-number as he hums a single eerie note without changing expression; the note passes around the table one person at a time until the entire group forms a strange four-part harmony and the chairman slowly lowers his pointer.",
    "On the harmony's sudden rhythmic shift, chairs scrape backward in perfect unison; the executives step onto their leather seats, climb onto the polished tabletop and kick ledgers aside as loose papers spiral through the lamplight.",
    "From a high symmetrical angle, the executives perform a sharply coordinated tabletop dance—heels clicking, coat tails snapping and arms forming angular clockwork patterns—then hit one impossible frozen pose on the final hum as the youngest calmly straightens his tie."
  ];
  if(isSmoresTutorial(v.idea))return[
    "At a glowing campfire, the upbeat woman leans into the vertical phone camera holding a marshmallow and says, “Perfect s’mores in fifteen seconds—go!”; the smiling man beside her raises a graham cracker like a starting flag.",
    "The man snaps a graham cracker into two clean squares, lays chocolate on one half and tells the lens, “Graham cracker, then chocolate.”",
    "The woman slowly rotates a marshmallow just above the coals until it turns evenly golden and says, “Toast it golden—not flaming.”",
    "The man catches the hot marshmallow between the chocolate and second cracker, presses once as the chocolate visibly softens, and says, “Stack it while it’s hot.”",
    "They take simultaneous bites; sticky marshmallow stretches between the halves as she laughs, “That crunch!” and he answers, “That melt!”",
    "They hold the finished s’mores toward the lens in a cheerful two-shot; he says, “Save this for your next campfire,” and she adds, “Bring napkins!” before both laugh."
  ];
  if(isTutorial(v.idea)){
    const topic=tutorialTopic(v.idea);
    return[
      `Two likable presenters face the phone camera with the finished ${topic}; the woman hooks the viewer with, “Here’s how to make ${topic}—fast.”`,
      `The man lays out the essential materials for ${topic} in a clean overhead view and names the first requirement while pointing directly to it.`,
      `The woman demonstrates the opening step close to camera, explaining one short practical tip while her hands make the action unmistakable.`,
      `The man takes over on a whip-pan, performs the decisive next step and warns against the most obvious mistake in one concise line.`,
      `They complete ${topic} together, reacting naturally as the result becomes visible and confirming success in alternating close-ups.`,
      `Both presenters show the finished ${topic} to the lens and deliver a friendly save-or-try-it call to action before sharing a spontaneous laugh.`
    ];
  }
  const expanded=storyEvents(expandStory(v.idea));
  return expanded.length?expanded:[finish(v.idea)];
}
function isClayCafe(idea){return /(?:modeling|modelling) cla(?:y|ry).*?(?:order|coffee|caf[eé]|barista)|(?:order|coffee|caf[eé]|barista).*?(?:modeling|modelling) cla(?:y|ry)/i.test(idea);}
function isBoardroomDance(idea){return /(?:business meeting|boardroom).*executives?.*(?:humm|sing).*(?:climb|table).*(?:dance|choreograph)/i.test(idea);}
function isTutorial(idea){return /how to|tutorial|demonstrat|explaining|walks? (?:us|you) through|step[- ]by[- ]step/i.test(idea);}
function isSmoresTutorial(idea){return isTutorial(idea)&&/s[’']?mores?/i.test(idea);}
function tutorialTopic(idea){
  const match=String(idea||"").match(/how to (?:make|build|create|prepare|cook|do)\s+(.+?)(?:\s+(?:in|with|for|using)\s+(?:this|a|the)\b|[.!?]|$)/i);
  return (match?.[1]||"it").trim().replace(/[,.]+$/g,"");
}
function storyEvents(idea){
  const text=sanitizeStyleReferences(String(idea||"").replace(/\r/g," ")),sentences=[];let current="",quoted=false;
  for(const ch of text){
    const wasQuoted=quoted;if(ch==='"')quoted=!quoted;
    current+=ch;
    if((!quoted&&/[.!?]/.test(ch))||(wasQuoted&&!quoted&&/[.!?]"$/.test(current))){if(current.trim())sentences.push(current.trim());current="";}
  }
  if(current.trim())sentences.push(current.trim());
  const events=[];
  for(const sentence of sentences){
    let part="",inside=false;
    for(let i=0;i<sentence.length;i++){
      const ch=sentence[i];if(ch==='"')inside=!inside;
      const tail=sentence.slice(i+6).trim();
      if(!inside&&sentence.slice(i,i+6).toLowerCase()===", and "&&/^(?:he|she|they|it|we|I|the|a|an|this|that|[A-Z][a-z])\b/.test(tail)){
        if(part.trim())events.push(part.trim().replace(/,$/,""));part="";i+=5;continue;
      }
      if(!inside&&sentence.slice(i,i+2)==="; "){
        if(part.trim())events.push(part.trim());part="";i+=1;continue;
      }
      part+=ch;
    }
    if(part.trim())events.push(part.trim());
  }
  return events.filter(event=>event&&!/^(?:make|use|keep|render|create)\s+(?:it|this|the video)\b/i.test(event));
}
function fitEvents(events,count){
  if(events.length===count)return events;
  if(events.length>count){
    const fitted=[];
    for(let i=0;i<count;i++){
      const start=Math.floor(i*events.length/count),end=Math.floor((i+1)*events.length/count);
      fitted.push(events.slice(start,Math.max(start+1,end)).map((event,index)=>index?lowerEventStart(event):event).join("; then "));
    }
    return fitted;
  }
  return Array.from({length:count},(_,i)=>events[Math.min(events.length-1,Math.floor(i*events.length/count))]);
}
function lowerEventStart(text){return String(text||"").replace(/^(It|The|A|An|He|She|They|We)\b/,word=>word.toLowerCase());}
function capitalizeEvent(text){return String(text||"").replace(/^(\s*["']?)([a-z])/,(_,prefix,letter)=>prefix+letter.toUpperCase());}
function eventDrivenTimeline(v,ranges,events,flow,continuous){
  const fitted=fitEvents(events,ranges.length);
  const grammar=isTutorial(v.idea)?[
    "Open in a personable handheld two-shot with the finished result visible immediately; keep the hook direct to camera and duck the music beneath speech.",
    "Jump cut to a crisp overhead or hand-level insert; preserve the presenters’ positions and synchronize every handling sound.",
    "Move into a tactile macro demonstration with quick autofocus breathing and firelight or practical light reacting naturally on faces and materials.",
    "Whip-pan with the handoff to the other presenter, then settle into a close three-quarter angle where the key action and spoken tip are both readable.",
    "Alternate tight bite-or-result details with genuine facial reactions; let the music lift between the two short lines without covering either voice.",
    "Finish on a friendly vertical two-shot and fast push toward the completed result, ending on laughter and a clean musical button."
  ]:flow==="Horror escalation and reveal"?[
    "Begin on a wide, low-light establishing frame with deep negative space; hold just long enough for the source of unease to register off-screen.",
    "Move into a low trailing tracking shot, keeping the dark space ahead dominant while fabric movement, footsteps and breathing locate the subject in stereo.",
    "Cut to a floor-level insert, then rack focus into the subject's point of view; isolate the suspicious light or sound with precise directional audio.",
    "Use an over-the-shoulder close shot and a nearly imperceptible push-in; hold on the face after the action so apprehension and the room's silence become visible.",
    "Cut frontally to the threatened space and release the reveal with one abrupt camera recoil; practical light catches tactile creature detail as the sound peaks exactly with its movement.",
    "Finish in a handheld retreat that preserves geography and screen direction, then land on a disturbing final composition rather than cutting off mid-action."
  ]:flow==="Dynamic action sequence"?[
    "Open with a wide geography shot that establishes every subject and the direction of travel.",
    "Cut low into fast lateral tracking, preserving screen direction and physical weight.",
    "Switch to a tight point-of-view insert motivated by the action and synchronized impact sound.",
    "Use a reaction close-up followed by a whip-pan into the next escalating event.",
    "Widen for the largest cause-and-effect payoff with credible debris, contact and scale.",
    "End on a decisive moving hero angle and hold briefly on the transformed aftermath."
  ]:flow==="Sitcom dialogue beats"?[
    "Open on a warm, readable two-shot or room-wide composition that establishes the everyday situation and who is present.",
    "Cut to the interruption or entrance from a motivated doorway or listener angle; preserve eyelines and room geography.",
    "Move into clean speaker coverage for the request, complaint or misunderstanding, keeping the spoken line short and natural.",
    "Cut to the listener reaction and response; allow a brief pause before the line so the comic timing can register.",
    "Use a contrasting reaction or reversal shot that clearly lands the consequence of the exchange.",
    "Finish on a simple payoff or reset to the original situation, holding long enough for the final reaction to land."
  ]:flow==="TV comedy scene beats"?[
    "Open with grounded episodic coverage that establishes the characters, location and immediate everyday problem.",
    "Cut on a motivated entrance, look or gesture into the next conversational beat.",
    "Use a medium speaker shot or two-shot for the key line, then preserve the listener in the established eyeline.",
    "Move to a restrained reaction close-up or insert as the situation becomes mildly more awkward or funny.",
    "Widen or reframe for the consequence without forcing exaggerated physical comedy.",
    "Finish on a light character reaction, dry line or small visual payoff that resolves the scene."
  ]:flow==="Stand-up performance"?[
    "Establish the comedian, microphone, stage and audience in one coherent room-wide view.",
    "Move to a confident medium shot as the comedian delivers the setup with natural gestures and clear lip synchronization.",
    "Hold or push slightly closer for the next line, preserving the performer’s timing and stage position.",
    "Use one selective audience reaction only when it supports the joke, then return immediately to the performer.",
    "Stay on the comedian through the strongest line or callback instead of cutting away unnecessarily.",
    "Finish on the performer’s reaction or a brief room-wide response after the final line lands."
  ]:flow==="Slapstick escalation"?[
    "Open wide enough to establish the person, obstacle, prop and complete physical geography of the gag.",
    "Show anticipation and the first failed attempt in a readable full- or medium-body frame.",
    "Cut closer only for the precise contact, prop detail or cause that triggers the next consequence.",
    "Return wider for the escalating physical result, preserving body position, screen direction and believable contact.",
    "Use a clear reaction shot or recovery beat before the final escalation.",
    "Finish on the strongest physical payoff and hold briefly on the aftermath or embarrassed reaction."
  ]:flow==="Sketch comedy beats"?[
    "Open with a fast, unmistakable setup that establishes the characters, situation and comic rule.",
    "Cut directly to the first complication or contradiction; avoid repeating information already established.",
    "Introduce a stronger escalation through a new action, line or prop while keeping continuity exact.",
    "Use a reaction or reversal shot that changes the audience’s understanding of the gag.",
    "Drive immediately into the decisive punchline rather than adding filler action.",
    "Hold on the final reaction or visual button long enough for the joke to register before the clip ends."
  ]:flow==="Dark comedy restraint"?[
    "Open with sober, grounded framing that treats the situation as completely serious.",
    "Introduce the awkward, ironic or inappropriate detail without changing to cartoonish camera language.",
    "Use a restrained medium or close shot for the dry response, leaving space for an uncomfortable pause.",
    "Cut to a specific reaction that reveals the absurdity without overplaying it.",
    "Escalate the consequence while maintaining calm composition and believable performance.",
    "Finish on the sharpest contrast, deadpan reaction or uncomfortable final image rather than a horror reveal."
  ]:flow==="Parody genre beats"?[
    "Open by convincingly establishing the visual grammar and stakes of the genre being spoofed.",
    "Use an authentic genre-specific camera move or composition before introducing the first exaggerated convention.",
    "Escalate one recognizable cliché through a clear action or line while keeping the filmmaking intentionally competent.",
    "Cut to a reaction that exposes the comic mismatch without abandoning the genre look.",
    "Push the chosen convention to an unmistakable but coherent extreme.",
    "Finish on a genre-authentic hero, reveal or dramatic button that functions as the parody payoff."
  ]:flow==="Surreal comedy progression"?[
    "Open on a completely coherent ordinary situation with calm, matter-of-fact framing.",
    "Introduce one impossible event clearly, preserving character identity, lighting and room geography.",
    "Cut to a restrained reaction that treats the impossible event as strangely normal.",
    "Escalate through a second absurd event that follows the same internal dream logic rather than random imagery.",
    "Reveal the consequence in a wider or contrasting composition while maintaining continuity.",
    "Finish on a precise surreal payoff or deadpan reaction that makes the absurd logic feel intentional."
  ]:[
    "Open with a specific wide establishing composition that clearly places the subject and immediate goal.",
    "Cut closer on motivated movement, using a new angle to reveal the next action and expression.",
    "Use a tactile insert or point-of-view shot for the critical detail, with synchronized source sound.",
    "Move to a contrasting reaction angle and push closer as the situation changes.",
    "Widen to reveal the consequence and its effect on the established environment.",
    "Finish on a specific close-up or final composition that visibly resolves the supplied event."
  ];
  return ranges.map((r,i)=>{
    const direction=grammar[Math.round(i*(grammar.length-1)/Math.max(1,ranges.length-1))];
    const transition=continuous?(i===0?"Begin one unbroken camera move; ":"Without cutting, reframe; "):i===0?"":"Cut on the preceding movement; ";
    return `${continuous?"BEAT":"SHOT"} ${i+1} [${formatTime(r[0])}–${formatTime(r[1])}]: ${transition}${direction} ${finish(capitalizeEvent(fitted[i]))}`;
  });
}
function shotPlanPrompt(v){
  const count=shotCount(v),flow=effectiveFlow(v);
  const brief={story:v.idea,seconds:v.duration,shotCount:count,visualStyle:v.style,artDirection:styleProfile(v.style),shotFlow:flow,camera:v.camera==="Automatic cinematic camera"?automaticCamera(v):v.camera,dialogueRequirement:v.dialogue||(dialogueRequested(v)?"Invent concise, natural dialogue spoken by the visible characters; keep lines short enough to fit the available clip duration.":"No dialogue required."),sound:v.soundEnabled?(v.sound||"Invent synchronized native stereo ambience and effects."):"Complete silence.",reference:v.referenceEnabled?(v.referenceLock||"Preserve the supplied reference image exactly."):"No reference image."};
  return `Return ONLY valid JSON for a ${count}-shot video story plan. Do not write markdown, headings, timestamps or production advice. The app will format the final prompt.

Invent the actual visible story. Every shot must contain a different, concrete action performed by the specified people or subjects. Flesh out the premise with specific props, expressions, choreography, physical cause and effect, environmental reactions and a decisive ending. Never write placeholders such as “establish the scene,” “the action unfolds,” “show the consequence,” “talks about,” or “resolves the idea.”

For each shot provide:
- action: 2–3 vivid sentences describing exactly what appears and happens on screen.
- camera: a specific framing, angle and motivated movement different from the other shots.
- dialogue: an array of exact spoken lines formatted as SPEAKER: words. If dialogue is requested, give at least two lines and preserve every requested speaker. Otherwise use an empty array.
- sound: exact synchronized ambience, effects and music behavior for that shot.

Use this exact schema and exactly ${count} shot objects:
{"continuity":"Specific stable character, wardrobe, location and lighting details shared by every shot.","shots":[{"action":"Concrete visible event.","camera":"Specific shot and movement.","dialogue":["SPEAKER: Exact words."],"sound":"Synchronized sound."}]}

CREATIVE BRIEF
${JSON.stringify(brief)}`;
}
function directGenerationPrompt(v){
  const dialogueRule=dialogueRequested(v)?"Include concise, natural dialogue in quotation marks, name each visible speaker, and invent the actual words when needed. Never summarize dialogue.":"Add dialogue only if it improves the story.";
  const mediumRule=mediumSpecificRule(v);
  return `Write one finished, copy-ready MiniMax Hailuo H3 video prompt. Expand the premise into a specific ${v.duration}-second visual story. Output only the prompt. Begin immediately with the visual description; do not add a title, heading or label such as “MiniMax Hailuo H3 Video Prompt.”

Start with a compact paragraph locking the ${v.style} look, characters, wardrobe, location and lighting. The selected visual style is authoritative: preserve its medium, rendering method, materials, palette, lighting and camera language; never replace it with a generic cinematic look or another medium. Complete every exact time range below. Each shot needs a different concrete story event, purposeful framing or camera move, visible performance and synchronized sound. Invent useful actions, props, reactions, cause and effect, and a decisive ending instead of repeating the premise. ${dialogueRule}

${mediumRule?`${mediumRule} This medium lock overrides any incompatible camera or shot-flow selection.`:""}

End with one concise quality lock covering identity, anatomy, geography, physics, reference fidelity and audiovisual synchronization. Stay below 7,000 characters.

EXACT TIMELINE
${timelineTemplate(v)}

USER'S PREMISE
${sanitizeStyleReferences(v.idea)}

SELECTED SETTINGS
Visual style: ${v.style}
Mandatory visual art direction: ${styleProfile(v.style)}
Camera: ${v.camera}
Shot flow: ${effectiveFlow(v)}
Format: ${v.duration} seconds, ${v.ratio}, ${v.resolution}
Sound: ${v.soundEnabled?(v.sound||"Native synchronized sound, ambience and effects."):"Silent."}
Dialogue or exact text: ${v.dialogue||"None supplied; invent it when the premise implies a conversation."}
Reference image: ${v.referenceEnabled?(v.referenceLock||"Preserve the supplied image."):"None."}
Avoid: ${v.avoid||"Continuity errors, duplicated anatomy and unreadable action."}`;
}
function stripModelReasoning(value,partial=false){
  let text=String(value||"").replace(/\r\n?/g,"\n");
  if(!text.trim())return "";
  // Some llama.cpp reasoning models expose reasoning inside the content stream.
  // Remove complete hidden-reasoning blocks first.
  text=text.replace(/<think>[\s\S]*?<\/think>/gi,"")
           .replace(/<thinking>[\s\S]*?<\/thinking>/gi,"")
           .replace(/<reasoning>[\s\S]*?<\/reasoning>/gi,"");
  // Certain GGUF templates omit the opening tag but still emit </think> before
  // the actual answer. In that case everything before the LAST closing tag is reasoning.
  const orphan=/<\/(?:think|thinking|reasoning)>/gi;
  let match,lastEnd=-1;
  while((match=orphan.exec(text))!==null)lastEnd=match.index+match[0].length;
  if(lastEnd>=0)text=text.slice(lastEnd);
  // While streaming, never display an unfinished hidden-reasoning block.
  const open=/<(?:think|thinking|reasoning)>/i.exec(text);
  if(open){
    if(partial)return text.slice(0,open.index).trim();
    text=text.slice(0,open.index);
  }
  text=text.replace(/<\/?(?:think|thinking|reasoning)>/gi,"");
  // Also handle plain-text wrappers used by some reasoning templates.
  const labelled=text.match(/^\s*(?:thinking|reasoning|analysis|thought process)\s*:\s*[\s\S]*?\n\s*(?:final answer|answer|final|response)\s*:\s*([\s\S]+)$/i);
  if(labelled)text=labelled[1];
  return clean(text);
}
function generationPromptForModel(model,v){
  const prompt=`${directGenerationPrompt(v)}\n\nOUTPUT RULE: Return only the finished production-ready H3 video prompt. Do not include analysis, reasoning, planning notes, <think> tags, explanations, preambles or commentary.`;
  // Qwen3 is a hybrid reasoning model. Keep its documented switch as an extra guard.
  return /(?:^|[/_-])qwen3(?::|[-_/]|$)/i.test(String(model||""))?`${prompt}\n\n/no_think`:prompt;
}
function shotPlanSchema(v){
  const shot={type:"object",additionalProperties:false,required:["action","camera","dialogue","sound"],properties:{action:{type:"string"},camera:{type:"string"},dialogue:{type:"array",items:{type:"string"}},sound:{type:"string"}}};
  return{type:"object",additionalProperties:false,required:["continuity","shots"],properties:{continuity:{type:"string"},shots:{type:"array",minItems:shotCount(v),maxItems:shotCount(v),items:shot}}};
}
function repairShotPlanPrompt(v,draft){
  return `${shotPlanPrompt(v)}

The previous JSON plan below was incomplete or generic. Replace it completely. Preserve exactly ${shotCount(v)} shots, write specific on-screen action in every shot and obey all dialogue requirements.

INVALID PLAN
${String(draft||"").slice(0,6000)}`;
}
function parseShotPlan(raw){
  try{
    const text=String(raw||"").replace(/^```(?:json)?\s*/i,"").replace(/\s*```$/i,"").trim();
    const start=text.indexOf("{"),end=text.lastIndexOf("}");if(start<0||end<=start)return null;
    return JSON.parse(text.slice(start,end+1));
  }catch{return null;}
}
function normalizeShotPlan(v,plan){
  if(!plan||typeof plan!=="object")return null;
  if(plan.plan&&typeof plan.plan==="object")plan=plan.plan;
  const rawShots=Array.isArray(plan.shots)?plan.shots:Array.isArray(plan.storyboard)?plan.storyboard:null;if(!rawShots)return null;
  return{
    continuity:String(plan.continuity||plan.consistency||`Keep the supplied subjects, wardrobe, café geography, materials and lighting consistent throughout.`).trim(),
    shots:rawShots.map(shot=>({
      action:String(shot?.action||shot?.visual||shot?.description||"").trim(),
      camera:String(shot?.camera||shot?.framing||shot?.shot||"").trim(),
      dialogue:normalizeDialogue(shot?.dialogue||shot?.lines),
      sound:String(shot?.sound||shot?.audio||"").trim()
    }))
  };
}
function normalizeDialogue(value){return (Array.isArray(value)?value:typeof value==="string"?[value]:[]).map(line=>String(line||"").trim()).filter(Boolean);}
function validShotPlan(v,plan){
  if(!plan||typeof plan.continuity!=="string"||plan.continuity.trim().length<15||!Array.isArray(plan.shots)||plan.shots.length!==shotCount(v))return false;
  const generic=/establish(?:es|ing)? (?:the )?(?:scene|subject|setting)|the action unfolds|show(?:s|ing)? (?:the )?(?:result|consequence)|decisive action|resolves? the idea|talks? about|voiceover continues|readable chain of cause and effect/i;
  const actions=new Set(),dialogue=[];
  for(const shot of plan.shots){
    if(!shot||typeof shot.action!=="string"||shot.action.trim().length<35||generic.test(shot.action)||typeof shot.camera!=="string"||shot.camera.trim().length<8)return false;
    if(v.soundEnabled&&(typeof shot.sound!=="string"||shot.sound.trim().length<5))return false;
    actions.add(shot.action.trim().toLowerCase());dialogue.push(...normalizeDialogue(shot.dialogue));
  }
  if(actions.size!==plan.shots.length||dialogueRequested(v)&&dialogue.length<2)return false;
  return true;
}
function formatPlannedDialogue(line){
  const cleanLine=String(line||"").replace(/^[“"]|[”"]$/g,"").trim(),match=cleanLine.match(/^([^:]{1,40}):\s*(.+)$/);
  return match?`${match[1].trim()} says, “${match[2].trim().replace(/^[“"]|[”"]$/g,"")}”`:`A visible speaker says, “${cleanLine}”`;
}
function renderShotPlan(v,plan){
  const camera=v.camera==="Automatic cinematic camera"?automaticCamera(v):finish(v.camera),continuous=v.flow==="One continuous shot";
  const lines=[`${v.duration}-second ${v.ratio} ${v.resolution} ${v.style.toLowerCase()} video. ${styleProfile(v.style)} ${continuityAnchor(v)} CONTINUITY: ${finish(plan.continuity)}`];
  timelineRanges(v).forEach((range,index)=>{
    const shot=plan.shots[index],dialogue=normalizeDialogue(shot.dialogue).map(formatPlannedDialogue).join(" "),transition=continuous?(index?"Without cutting, transition through visible camera movement. ":"Begin one unbroken take. "):(index?"Cut on motivated movement. ":"Open on this shot. ");
    lines.push(`${continuous?"BEAT":"SHOT"} ${index+1} [${formatTime(range[0])}–${formatTime(range[1])}]: ${transition}${finish(shot.camera)} ${finish(shot.action)}${dialogue?` ${dialogue}`:""}${v.soundEnabled?` Sound: ${finish(shot.sound)}`:" Sound: complete silence."}`);
  });
  if(v.referenceEnabled)lines.push(`REFERENCE LOCK: The supplied image defines the subject's appearance${v.referenceLock?`, including ${v.referenceLock}`:""}; preserve that exact design, proportions, colors and identity in every shot.`);
  if(v.dialogue)lines.push(`DIALOGUE LOCK: Preserve the user's exact requested wording and speaker assignment: ${finish(v.dialogue)}`);
  const audio=v.soundEnabled?(v.sound?finish(v.sound):"native stereo ambience, tactile close-up effects and reactions synchronized exactly to their visible source"):"complete silence";
  lines.push(`FINAL QUALITY LOCK: ${camera} Preserve subject identity, wardrobe, environment geography, screen direction, lighting logic and story progression across every cut. Use believable weight, inertia, contact, occlusion and cause-and-effect motion. Sound treatment: ${finish(audio)} No continuity jumps, duplicated anatomy, accidental extra subjects, unreadable action, dialogue placeholders or generic filler imagery.${v.avoid?` Exclude ${finish(v.avoid)}`:""}`);
  return lines.join("\n\n").slice(0,12000);
}
function plannerPrompt(v){
  const inferredFlow=effectiveFlow(v),creativeBrief={...v,inferredShotFlow:inferredFlow,visualArtDirection:styleProfile(v.style),cameraDirection:v.camera==="Automatic cinematic camera"?automaticCamera(v):v.camera,editingGrammar:flowProfile(inferredFlow,v.duration)};
  const dialogueRule=dialogueRequested(v)?"DIALOGUE IS MANDATORY. Write the actual concise words spoken inside the timed shots, in quotation marks, and identify the visible speaker for every line. If the user asks people to explain something but does not supply exact wording, invent natural, specific lines that perform the explanation. Never substitute phrases such as ‘talks about,’ ‘explains the process,’ ‘voiceover continues,’ or ‘full dialogue here.’ Preserve every requested speaker and let them visibly take turns.":"Do not invent dialogue unless it materially helps the supplied idea.";
  return `You are a visual storyteller writing one finished, copy-ready prompt for MiniMax Hailuo H3. The user's text is a STORY SEED, not the finished prompt. Invent the actual on-screen story and exploit H3's strengths in multi-shot sequencing, expressive motion, camera control, physical interaction, continuity and synchronized native stereo sound.

OUTPUT ONLY THE FINISHED VIDEO PROMPT. Start with one compact OVERALL LOOK AND CONTINUITY paragraph defining format, stable subject appearance, environment geography, lighting direction and style-specific art direction. Then reproduce every line of the supplied timeline below and complete it. Times are mandatory and immutable: they must begin at 0:00.0, remain contiguous without gaps or overlaps, and end at exactly ${formatTime(v.duration)}. Each segment must describe a distinct visible event, shot scale or lens perspective, motivated camera movement, transition, physical motion, performance or expression, style-specific lighting, and synchronized sound. Every cut needs a different visual purpose; do not repeat an angle. Preserve screen direction and cause-and-effect action across cuts. ${v.flow==="One continuous shot"?"This is one unbroken take: use the timed BEAT labels, never cut, and connect every reframe through visible camera movement.":"Use the SHOT labels and make every edit concrete."}

${dialogueRule}

End with one FINAL QUALITY LOCK paragraph covering identity, wardrobe, anatomy, reference-image fidelity when enabled, spatial continuity, believable physics, audiovisual synchronization and explicit exclusions. Do not answer with generic production advice, a synopsis, screenplay dialogue, or untimed prose. Supply the story, shots and sounds yourself. Preserve named subjects and the user's central idea. Do not add unrelated lore, characters, signs, logos or claims. Respect exact quoted wording. If the user references a living filmmaker, do not name or imitate that person; translate the request into non-identifying visual traits. Keep the selected medium consistent and stay below 7,000 characters.

MANDATORY TIMELINE TO COMPLETE
${timelineTemplate(v)}

QUALITY TEST: Several concrete story events must be newly invented from the seed. The result is invalid if any time range is missing, altered, generic, or left empty.

USER SETTINGS
${JSON.stringify(creativeBrief,null,2)}`;
}

function currentLlmBackend(){return $("llmBackend")?.value||"gguf";}
function selectedLocalModel(){return currentLlmBackend()==="gguf"?$("ggufModel").value:$("ollamaModel").value;}
function modelDisplayName(model){const s=String(model||"");return s.split(/[\\/]/).pop()||s;}
function updateLlmBackendUI(){
  const backend=currentLlmBackend();
  $("ollamaSettings").hidden=backend!=="ollama";$("ggufSettings").hidden=backend!=="gguf";
  const disabled=backend==="builtin";$("loadModelButton").hidden=disabled;$("unloadModelButton").hidden=disabled;
  if(disabled){setModelLoadUI("","Built-in prompt engine","No external LLM will be loaded. Prompt generation uses the deterministic H3 story engine.","READY");$("ollamaMessage").textContent="Built-in prompt engine selected.";$("ollamaMessage").className="settings-message good";}
}
async function refreshLlamaBundleStatus(showDialog=false){
  try{
    const r=await fetch(`/api/gguf/bundle/status?t=${Date.now()}`,{cache:"no-store"});const d=await r.json();if(!r.ok)throw new Error(d.error||"Could not inspect llama.cpp bundle");
    const btn=$("downloadLlamaBundleButton");
    if(d.installed&&d.runner){
      $("llamaRunner").value=d.runner;saveSettings();btn.hidden=true;
      if(state.llamaDownloadTimer){clearInterval(state.llamaDownloadTimer);state.llamaDownloadTimer=null;}
    }else{btn.hidden=false;btn.textContent=d.active?"Show llama download progress":"Download llama server bundle";}
    updateLlamaDownloadDialog(d,showDialog);
    return d;
  }catch(e){return null;}
}
function formatBytes(n){n=Number(n||0);if(!n)return "";const u=["B","KB","MB","GB"];let i=0;while(n>=1024&&i<u.length-1){n/=1024;i++;}return `${n.toFixed(i?1:0)} ${u[i]}`;}
function updateLlamaDownloadDialog(d,showDialog=false){
  if(!d)return;const dlg=$("llamaDownloadDialog");
  const p=Math.max(0,Math.min(100,Number(d.percent||0)));$("llamaDownloadBar").style.width=`${p}%`;$("llamaDownloadPercent").textContent=`${Math.round(p)}%`;
  $("llamaDownloadStage").textContent=(d.stage||"idle").replace(/_/g," ");$("llamaDownloadMessage").textContent=d.message||"Preparing llama.cpp bundle…";
  $("llamaDownloadBytes").textContent=d.total?`${formatBytes(d.downloaded)} / ${formatBytes(d.total)} · You can close this popup; the download continues.`:"You can close this popup; the download continues in the background.";
  const err=$("llamaDownloadError");err.hidden=!d.error;err.textContent=d.error||"";
  if(showDialog&&!dlg.open)dlg.showModal();
}
async function startLlamaBundleDownload(){
  const note=$("ollamaMessage"),btn=$("downloadLlamaBundleButton");
  note.textContent="Checking llama.cpp bundle status…";note.className="settings-message";if(btn)btn.disabled=true;
  try{
    const existing=await refreshLlamaBundleStatus(false);
    if(existing?.installed){note.textContent="llama.cpp server bundle is already installed and selected.";note.className="settings-message good";return;}
    const r=await fetch("/api/gguf/bundle/download",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
    const d=await r.json();if(!r.ok)throw new Error(d.error||"Could not start llama.cpp download");
    note.textContent="llama.cpp download started. Progress is shown in the download window.";note.className="settings-message good";
    // Close Settings before showing the progress dialog.  This avoids WebEngine
    // builds where a second modal dialog can appear behind the first one.
    const settings=$("settingsDialog");if(settings?.open)settings.close();
    updateLlamaDownloadDialog(d.status||d,true);
    if(!state.llamaDownloadTimer)state.llamaDownloadTimer=setInterval(async()=>{
      const st=await refreshLlamaBundleStatus(false);
      if(st&&!st.active&&(st.installed||st.stage==="failed")){
        clearInterval(state.llamaDownloadTimer);state.llamaDownloadTimer=null;
        if(st.installed){note.textContent="llama.cpp server bundle installed and selected automatically.";note.className="settings-message good";}
      }
    },500);
  }catch(e){
    const message=e?.message||String(e);note.textContent=message;note.className="settings-message";
    updateLlamaDownloadDialog({stage:"failed",message:"llama.cpp bundle download could not start",error:message,percent:0},true);
  }finally{if(btn)btn.disabled=false;}
}
async function browseGgufFolder(){
  const r=await fetch("/api/gguf/browse-folder",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({initial:$("ggufFolder").value})});const d=await r.json();if(!r.ok)return show(d.error||"Folder picker failed",false);if(d.path){$("ggufFolder").value=d.path;saveSettings();await scanGgufFolder();}
}
async function browseLlamaRunner(){
  const r=await fetch("/api/gguf/browse-runner",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({initial:$("llamaRunner").value})});const d=await r.json();if(!r.ok)return show(d.error||"Runner picker failed",false);if(d.path){$("llamaRunner").value=d.path;saveSettings();}
}
async function scanGgufFolder(){
  const note=$("ollamaMessage");note.textContent="Scanning GGUF folder…";note.className="settings-message";
  try{const r=await fetch("/api/gguf/scan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({folder:$("ggufFolder").value})});const d=await r.json();if(!r.ok)throw new Error(d.error||"GGUF scan failed");const current=$("ggufModel").value;$("ggufModel").innerHTML='<option value="">Choose a GGUF model…</option>'+d.models.map(m=>`<option value="${escapeHtml(m.path)}">${escapeHtml(m.relative)} · ${(Number(m.size||0)/1073741824).toFixed(1)} GB</option>`).join("");if(current&&[...$("ggufModel").options].some(o=>o.value===current))$("ggufModel").value=current;note.textContent=`Found ${d.models.length} GGUF model${d.models.length===1?"":"s"}. Choose one, then click Load selected model.`;note.classList.add("good");saveSettings();}catch(e){note.textContent=e.message;note.className="settings-message";}
}
async function ggufStatus(){const r=await fetch(`/api/gguf/status?t=${Date.now()}`,{cache:"no-store"});const d=await r.json();if(!r.ok)throw new Error(d.error||"Could not inspect local GGUF server");return d;}
async function preloadGgufModel(model){
  if(!model){setModelLoadUI("error","No GGUF model selected","Select a GGUF folder, scan it, and choose a model first.","ERROR");return false;}
  const runner=$("llamaRunner").value.trim();if(!runner){setModelLoadUI("error","No llama-server selected","Browse to llama-server.exe first.","ERROR");return false;}
  const current=await ggufStatus().catch(()=>({}));if(current.ready&&current.model===model){state.modelReady=model;setModelLoadUI("ready",`${modelDisplayName(model)} is ready`,`llama.cpp local server · ${Number(current.ctx||0).toLocaleString()} context`,`READY`);return true;}
  if(!(await confirmLlmLoadDuringGeneration()))return false;
  state.modelLoadName=model;state.modelLoadStarted=Date.now();setModelLoadUI("loading",`Loading ${modelDisplayName(model)}…`,`Starting llama-server and loading the selected GGUF model.`,"0:00");clearInterval(state.modelLoadTimer);state.modelLoadTimer=setInterval(()=>{$("modelLoadElapsed").textContent=formatElapsed(Date.now()-state.modelLoadStarted);},250);
  try{const r=await fetch("/api/gguf/load",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({runner,model,ctx:Number($("ggufContext").value),timeout:240})});const d=await r.json();if(!r.ok)throw new Error(d.error||"Could not load GGUF model");state.modelReady=model;setModelLoadUI("ready",`${modelDisplayName(model)} is ready`,`llama.cpp on port ${d.port} · ${Number(d.ctx||0).toLocaleString()} context`,formatElapsed(Number(d.load_seconds||0)*1000));$("ollamaMessage").textContent="Local GGUF model ready.";$("ollamaMessage").className="settings-message good";return true;}catch(e){state.modelReady="";setModelLoadUI("error",`Could not load ${modelDisplayName(model)}`,e.message,"ERROR");throw e;}finally{clearInterval(state.modelLoadTimer);state.modelLoadTimer=null;state.modelLoadName="";}
}
async function autoSelectOllamaModel(){
  try{
    const r=await fetch(`/api/ollama/tags?url=${encodeURIComponent($("ollamaUrl").value)}`);
    if(!r.ok)return"";
    const d=await r.json();const models=(d.models||[]).filter(m=>!/(embed|embedding)/i.test(m.name||m.model||""));
    if(!models.length)return"";
    models.sort((a,b)=>modelScore(b)-modelScore(a));
    const chosen=models[0].name||models[0].model||"";
    $("ollamaModel").innerHTML=models.map(m=>{const name=m.name||m.model;return `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`;}).join("");
    $("ollamaModel").value=chosen;saveSettings();return chosen;
  }catch{return"";}
}
function modelScore(m){
  const n=(m.name||m.model||"").toLowerCase(),gb=Number(m.size||0)/1073741824;let score=0;
  if(/qwen2\.5.*7b.*instruct|qwen2\.5:7b/.test(n))score+=180;
  else if(/qwen3.*(?:7b|8b|9b)/.test(n))score+=115;
  else if(/qwen.*(?:7b|8b|9b)/.test(n))score+=140;
  else if(/gemma.*(?:7b|8b|9b)/.test(n))score+=135;
  else if(/mistral.*(?:7b|8b|9b)/.test(n))score+=130;
  else if(/llama.*(?:7b|8b|9b)/.test(n))score+=125;
  else if(/qwen.*(?:12b|14b)/.test(n))score+=90;
  else if(/gemma4:12b|gemma4.*12b/.test(n))score+=80;
  else if(/qwen|llama|gemma|mistral|phi/.test(n))score+=45;
  if(gb>16)score-=100;else if(gb>11)score-=55;else if(gb>=3&&gb<=9)score+=25;
  if(/vision|vl/.test(n)&&!/gemma4/.test(n))score-=10;return score;
}
function modelNamesMatch(a,b){return String(a||"").replace(/:latest$/i,"")===String(b||"").replace(/:latest$/i,"");}
function formatBytes(bytes){const value=Number(bytes)||0;if(!value)return"size unavailable";const units=["B","KB","MB","GB"],index=Math.min(3,Math.floor(Math.log(value)/Math.log(1024)));return`${(value/1024**index).toFixed(index===3?1:0)} ${units[index]}`;}
function formatElapsed(ms){const seconds=Math.max(0,Math.floor(ms/1000));return`${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,"0")}`;}

function confirmLlmLoadDuringGeneration(){
  if(!window.__minimaxGenerationRunning)return Promise.resolve(true);
  return new Promise(resolve=>{
    const existing=document.getElementById("minimaxLlmWarningOverlay");if(existing)existing.remove();
    const overlay=document.createElement("div");overlay.id="minimaxLlmWarningOverlay";
    overlay.style.cssText="position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.72);display:flex;align-items:center;justify-content:center;padding:24px";
    const box=document.createElement("div");
    box.style.cssText="max-width:620px;width:min(620px,92vw);background:#101720;border:1px solid #395269;border-radius:14px;padding:22px;box-shadow:0 20px 70px rgba(0,0,0,.55);color:#f4f7fb;font:16px/1.45 system-ui,-apple-system,Segoe UI,sans-serif";
    box.innerHTML=`<div style="font-size:21px;font-weight:700;margin-bottom:10px;color:#82e7ff">Generation job is running</div><div style="margin-bottom:18px">Loading a local LLM now can fill GPU VRAM and make the current video generation much slower.</div>`;
    const buttons=document.createElement("div");buttons.style.cssText="display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end";
    const waitBtn=document.createElement("button");waitBtn.textContent="OK, I will wait or use the built-in templates";
    const useBtn=document.createElement("button");useBtn.textContent="I know, I want to use the LLM anyway";
    for(const b of [waitBtn,useBtn])b.style.cssText="padding:10px 14px;border-radius:8px;border:1px solid #45647d;background:#172330;color:#fff;cursor:pointer;font:inherit";
    useBtn.style.borderColor="#d98a00";
    waitBtn.onclick=()=>{overlay.remove();resolve(false);};
    useBtn.onclick=()=>{overlay.remove();resolve(true);};
    buttons.append(waitBtn,useBtn);box.append(buttons);overlay.append(box);document.body.append(overlay);
  });
}
function runningModelDetail(model){
  const total=Number(model?.size)||0,vram=Number(model?.size_vram)||0,gpu=total?Math.min(100,Math.round(vram/total*100)):0,processor=gpu>=98?"100% GPU":gpu?`${gpu}% GPU / ${100-gpu}% CPU`:"CPU or allocation unavailable";
  return `${processor} · ${formatBytes(vram)} VRAM · ${Number(model?.context_length||4096).toLocaleString()} context${model?.details?.quantization_level?` · ${model.details.quantization_level}`:""}`;
}
async function requireGpuResident(model){
  const running=(await runningModels()).find(item=>modelNamesMatch(item.name||item.model,model));
  if(!running)throw new Error(`${model} is no longer resident. Load it again before generating.`);
  const total=Number(running.size)||0,vram=Number(running.size_vram)||0,gpu=total?Math.round(vram/total*100):100;
  if(total&&gpu<90)throw new Error(`Ollama loaded only ${gpu}% of ${model} on the GPU. That would make generation impractically slow. Close other GPU-heavy programs, unload the model, and load it again.`);
  return running;
}
function setModelLoadUI(mode,title,detail,elapsed){
  const panel=$("modelLoadPanel");panel.hidden=false;panel.className=`model-load-panel ${mode||""}`.trim();$("modelLoadTitle").textContent=title;$("modelLoadDetail").textContent=detail;$("modelLoadElapsed").textContent=elapsed||"";
  $("loadModelButton").disabled=mode==="loading";$("unloadModelButton").disabled=mode==="loading"||mode==="error";
  $("cancelModelLoadButton").hidden=mode!=="loading"||!state.modelLoadId;$("cancelModelLoadButton").disabled=false;
  $("ollamaModel").disabled=mode==="loading";$("testOllamaButton").disabled=mode==="loading";if($("ggufModel"))$("ggufModel").disabled=mode==="loading";if($("scanGgufButton"))$("scanGgufButton").disabled=mode==="loading";if($("llmBackend"))$("llmBackend").disabled=mode==="loading";
  if(!state.timer&&!state.versionMismatch){$("generateButton").disabled=mode==="loading";$("regenerateButton").disabled=mode==="loading";}
}
async function runningModels(){
  const r=await fetch(`/api/ollama/ps?url=${encodeURIComponent($("ollamaUrl").value)}&t=${Date.now()}`,{cache:"no-store"}),data=await r.json();if(!r.ok)throw new Error(data.error||"Could not inspect Ollama memory.");return data.models||[];
}
async function refreshSelectedModelState(){
  const backend=currentLlmBackend();if(backend==="builtin")return updateLlmBackendUI();
  const model=selectedLocalModel();if(!model)return setModelLoadUI("","No model selected",backend==="gguf"?"Select a GGUF folder and model above.":"Find and select an installed Ollama model above.","IDLE");
  if(backend==="gguf"){
    try{const s=await ggufStatus();if(s.ready&&s.model===model){state.modelReady=model;setModelLoadUI("ready",`${modelDisplayName(model)} is ready`,`llama.cpp local server · ${Number(s.ctx||0).toLocaleString()} context`,`READY`);}else{state.modelReady="";setModelLoadUI("",`${modelDisplayName(model)} is not loaded`,`Click Load selected model, or it will load automatically before generation.`,`IDLE`);}}catch(e){setModelLoadUI("error","GGUF status unavailable",e.message,"ERROR");}return;
  }
  try{const running=(await runningModels()).find(item=>modelNamesMatch(item.name||item.model,model));if(running){state.modelReady=model;setModelLoadUI("ready",`${model} is ready`,runningModelDetail(running),"READY");}else if(!state.modelLoadPromise){state.modelReady="";setModelLoadUI("",`${model} is not loaded`,`Click Load selected model, or it will preload automatically before generation.`,`IDLE`);}}catch(e){if(!state.modelLoadPromise)setModelLoadUI("error","Ollama status unavailable",e.message,"ERROR");}
}
async function preloadSelectedModel(){const backend=currentLlmBackend();if(backend==="builtin")return true;const model=selectedLocalModel();if(backend==="gguf")return preloadGgufModel(model);if(!model)return setModelLoadUI("error","No model selected","Find and select an installed Ollama model first.","ERROR");return preloadModel(model);}
async function preloadModel(model){
  if(state.modelReady&&modelNamesMatch(state.modelReady,model)){await refreshSelectedModelState();if(state.modelReady)return true;}
  if(state.modelLoadPromise&&modelNamesMatch(state.modelLoadName,model))return state.modelLoadPromise;
  if(!(await confirmLlmLoadDuringGeneration()))return false;
  state.modelLoadName=model;state.modelLoadStarted=Date.now();state.modelLoadCancelled=false;state.modelLoadId=globalThis.crypto?.randomUUID?.()||`load-${Date.now()}-${Math.random().toString(16).slice(2)}`;state.modelLoadController=new AbortController();
  state.modelLoadPromise=(async()=>{
    try{
      const resident=(await runningModels()).find(item=>modelNamesMatch(item.name||item.model,model));if(resident){state.modelReady=model;setModelLoadUI("ready",`${model} is ready`,runningModelDetail(resident),"READY");return true;}
      setModelLoadUI("loading",`Loading ${model}…`,`Ollama does not report a percentage; the animated bar shows that loading is active and the elapsed time is exact.`,"0:00");
      clearInterval(state.modelLoadTimer);state.modelLoadTimer=setInterval(()=>{$("modelLoadElapsed").textContent=formatElapsed(Date.now()-state.modelLoadStarted);},250);
      const poll=setInterval(async()=>{try{const found=(await runningModels()).find(item=>modelNamesMatch(item.name||item.model,model));if(found)$("modelLoadDetail").textContent=`Model is resident (${runningModelDetail(found)}); finishing initialization…`;}catch{}},1500);
      let result;
      try{const r=await fetch("/api/ollama/model",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url:$("ollamaUrl").value,model,action:"load",load_id:state.modelLoadId}),signal:state.modelLoadController.signal});result=await r.json();if(!r.ok)throw new Error(result.error||"Ollama could not load the model.");}finally{clearInterval(poll);}
      const running=(await runningModels()).find(item=>modelNamesMatch(item.name||item.model,model));if(!running)throw new Error("Ollama completed the load request, but the model is not resident in memory.");
      state.modelReady=model;const measured=Number(result.load_duration)?formatElapsed(Number(result.load_duration)/1e6):formatElapsed(Date.now()-state.modelLoadStarted);setModelLoadUI("ready",`${model} is ready`,runningModelDetail(running),measured);
      $("ollamaMessage").textContent=`Ready. Model loaded in ${measured} and will remain available for 15 minutes after use.`;$("ollamaMessage").className="settings-message good";return true;
    }catch(e){state.modelReady="";if(state.modelLoadCancelled||e.name==="AbortError"){setModelLoadUI("",`Loading ${model} was cancelled`,`Choose another model and click Load selected model when ready.`,"CANCELLED");$("ollamaMessage").textContent="Model loading cancelled.";$("ollamaMessage").className="settings-message";return false;}setModelLoadUI("error",`Could not load ${model}`,e.message,"ERROR");$("ollamaMessage").textContent=e.message;$("ollamaMessage").className="settings-message";throw e;}
    finally{clearInterval(state.modelLoadTimer);state.modelLoadTimer=null;state.modelLoadPromise=null;state.modelLoadName="";state.modelLoadController=null;state.modelLoadId="";state.modelLoadCancelled=false;$("cancelModelLoadButton").hidden=true;}
  })();return state.modelLoadPromise;
}
async function cancelModelLoad(){
  const loadId=state.modelLoadId;if(!loadId||!state.modelLoadPromise)return;
  state.modelLoadCancelled=true;$("cancelModelLoadButton").disabled=true;$("modelLoadTitle").textContent=`Cancelling ${state.modelLoadName}…`;$("modelLoadDetail").textContent="Closing the active Ollama load request.";
  try{await fetch("/api/ollama/model-cancel",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({load_id:loadId})});}catch{}
  finally{state.modelLoadController?.abort();}
}
async function unloadSelectedModel(){
  const backend=currentLlmBackend(),model=selectedLocalModel();if(backend==="builtin"||!model)return;
  $("loadModelButton").disabled=true;$("unloadModelButton").disabled=true;setModelLoadUI("loading",`Unloading ${modelDisplayName(model)}…`,`Releasing model memory and VRAM.`,"");
  try{if(backend==="gguf"){const r=await fetch("/api/gguf/unload",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"}),data=await r.json();if(!r.ok)throw new Error(data.error||"Could not unload GGUF model");}else{const r=await fetch("/api/ollama/model",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url:$("ollamaUrl").value,model,action:"unload"})}),data=await r.json();if(!r.ok)throw new Error(data.error||"Ollama could not unload the model.");}state.modelReady="";setModelLoadUI("",`${modelDisplayName(model)} is unloaded`,`Its memory has been released. Load it again before generating.`,`IDLE`);}catch(e){setModelLoadUI("error",`Could not unload ${modelDisplayName(model)}`,e.message,"ERROR");}finally{$("loadModelButton").disabled=false;}
}
async function generate(){
  if(state.versionMismatch){show("Reload the current app version before generating.",false);return;}
  const v=values();if(!v.idea){show("Start by describing what should happen in the video.",false);$("idea").focus();return;}
  resetImagePrompt();
  const backend=currentLlmBackend();let model=selectedLocalModel(),generating=false,hardTimer=null;
  try{
    if(backend==="ollama"&&!model)model=await autoSelectOllamaModel();
    if(backend==="builtin"||!model){$("promptOutput").value=enforceVideoStyle(v,formatGeneratedPrompt(v,quickDraft(v)));await recordHistory(v,$("promptOutput").value);show("Time-coded H3 story prompt generated. Select a local LLM in Settings for even more imaginative expansion.",true);}
    else{
      if(!(backend==="gguf"?await preloadGgufModel(model):await preloadModel(model)))return;
      if(backend==="ollama")await requireGpuResident(model);
      setGenerating(true);generating=true;state.abortReason="";state.controller=new AbortController();
      const hardLimit=Number($("timeout").value)*1000;
      hardTimer=setTimeout(()=>{state.abortReason="timeout";state.controller?.abort();},hardLimit);
      const tokenBudget=currentLlmBackend()==="gguf"?7000:(v.duration>=13?520:v.duration>=9?440:360);
      $("promptOutput").value="";update();
      const onStream=(partial,activity)=>{const visible=stripModelReasoning(partial,true);if(visible){$("promptOutput").value=visible;$("promptOutput").scrollTop=$("promptOutput").scrollHeight;}else if(partial){$("promptOutput").value="";}$("generationLabel").textContent=(activity?.thinking||(!visible&&partial))?`Local model reasoning detected; waiting for final prompt…`:"Receiving the prompt from the local LLM…";update();};
      let streamed=await requestOllamaStream(model,generationPromptForModel(model,v),.82,tokenBudget,state.controller.signal,onStream),result=stripModelReasoning(streamed.response,false);
      if(!result)throw new Error("The local LLM returned an empty response.");
      result=enforceVideoStyle(v,formatGeneratedPrompt(v,result));
      $("promptOutput").value=result;
      await recordHistory(v,$("promptOutput").value);
      const rate=streamed.eval_count&&streamed.eval_duration?streamed.eval_count/(streamed.eval_duration/1e9):0;
      show(`Prompt generated directly with ${modelDisplayName(model)}${rate?` at ${rate.toFixed(1)} tokens/sec`:""}.`,true);
    }
  }catch(e){if(e.name==="AbortError"&&state.abortReason==="timeout")show(`Generation stopped at the selected ${$("timeout").selectedOptions[0].textContent} wall-clock limit. The local LLM was running too slowly; no substitute prompt was inserted.`,false);else if(e.name==="AbortError")show("Generation cancelled.",false);else show(`${e.message} No substitute prompt was inserted.`,false);}finally{clearTimeout(hardTimer);state.abortReason="";if(generating)setGenerating(false);update();}
}
async function recordHistory(v,output){
  try{await fetch("/api/history",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"add",values:v,output})});}catch{}
}
async function openHistory(){
  $("historyDialog").showModal();$("historySearch").value="";$("historyList").innerHTML='<p class="history-empty">Loading history…</p>';
  try{const r=await fetch(`/api/history?t=${Date.now()}`,{cache:"no-store"});if(!r.ok)throw 0;state.historyItems=(await r.json()).items||[];renderHistory();}catch{$("historyList").innerHTML='<p class="history-empty">Prompt history could not be loaded.</p>';}
}
function renderHistory(){
  const list=$("historyList"),query=$("historySearch").value.trim().toLowerCase();
  const items=state.historyItems.filter(item=>!query||`${item.title||""} ${item.output||""} ${item.values?.style||""}`.toLowerCase().includes(query));
  if(!items.length){list.innerHTML=`<p class="history-empty">${state.historyItems.length?"No history entries match that search.":"No prompts have been generated yet."}</p>`;return;}
  list.innerHTML=items.map(item=>{const v=item.values||{},when=new Date(item.created_at).toLocaleString([],{dateStyle:"medium",timeStyle:"short"});return `<article class="history-entry" data-history-id="${escapeHtml(item.id)}"><div><h3>${escapeHtml(item.title||v.idea||"Untitled prompt")}</h3><p>${escapeHtml(when)}</p><div class="history-meta"><span>${escapeHtml(v.style||"Style unavailable")}</span><span>${escapeHtml(String(v.duration||"?"))} sec</span><span>${escapeHtml(v.ratio||"?")}</span><span>${escapeHtml(v.flow||"Shot flow unavailable")}</span></div></div><div class="history-actions"><button class="history-load">Restore</button><button class="history-delete">Delete</button></div></article>`;}).join("");
  list.querySelectorAll(".history-entry").forEach(card=>{card.onclick=e=>{if(!e.target.closest(".history-delete"))loadHistoryEntry(card.dataset.historyId);};card.querySelector(".history-delete").onclick=e=>{e.stopPropagation();deleteHistoryEntry(card.dataset.historyId);};});
}
function loadHistoryEntry(id){
  const item=state.historyItems.find(entry=>entry.id===id);if(!item)return;const v=item.values||{};
  [["idea",v.idea],["dialogue",v.dialogue],["sound",v.sound],["avoid",v.avoid],["referenceLock",v.referenceLock]].forEach(([id,value])=>{if(typeof value==="string")$(id).value=value;});
  [["style",v.style],["camera",v.camera],["flow",v.flow],["resolution",v.resolution]].forEach(([id,value])=>{if(value&&[...$(id).options].some(option=>option.value===value))$(id).value=value;});
  $("duration").value=Math.max(4,Math.min(20,Number(v.duration)||8));state.ratio=["16:9","9:16","1:1","21:9"].includes(v.ratio)?v.ratio:"16:9";
  document.querySelectorAll("#ratioButtons button").forEach(button=>button.classList.toggle("active",button.dataset.value===state.ratio));
  $("soundEnabled").checked=v.soundEnabled!==false;$("referenceEnabled").checked=Boolean(v.referenceEnabled);$("referenceField").hidden=!$("referenceEnabled").checked;
  $("optionalDetails").open=Boolean(v.dialogue||v.sound||v.avoid||v.referenceEnabled||v.referenceLock);resetImagePrompt();$("promptOutput").value=item.output||"";update();$("historyDialog").close();$("idea").scrollIntoView({behavior:"smooth",block:"center"});show("Prompt and all settings restored from history.",true);
}
async function deleteHistoryEntry(id){
  if(!confirm("Delete this prompt from history?"))return;
  try{const r=await fetch("/api/history",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"delete",id})});if(!r.ok)throw 0;state.historyItems=state.historyItems.filter(item=>item.id!==id);renderHistory();}catch{alert("That history entry could not be deleted.");}
}
function ollamaOptions(model,temperature,numPredict){
  // Let Ollama use the model's native GPU, context and batch configuration—the
  // same fast path used by `ollama run`. Only creativity and output length are
  // application concerns.
  const isGemma4=/gemma4/i.test(model);return{temperature:isGemma4&&temperature>.6?1:temperature,num_predict:numPredict};
}
async function requestOllama(model,prompt,temperature,numPredict,signal,format){
  if(currentLlmBackend()==="gguf"){const r=await fetch("/api/gguf/generate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt,temperature,num_predict:numPredict,timeout:Number($("timeout").value)}),signal});const d=await r.json();if(!r.ok)throw new Error(d.error||"The local GGUF model could not generate the prompt.");return d.response;}
  const r=await fetch("/api/ollama/generate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({url:$("ollamaUrl").value,model,prompt,timeout:Number($("timeout").value),format,options:ollamaOptions(model,temperature,numPredict)}),signal});
  const data=await r.json();if(!r.ok)throw new Error(data.error||"The local model could not generate the prompt.");return data.response;
}
async function requestOllamaStream(model,prompt,temperature,numPredict,signal,onUpdate){
  const isGguf=currentLlmBackend()==="gguf";
  const endpoint=isGguf?"/api/gguf/generate-stream":"/api/ollama/generate-stream";
  const requestBody=isGguf?{prompt,temperature,num_predict:numPredict,timeout:Number($("timeout").value)}:{url:$("ollamaUrl").value,model,prompt,timeout:Number($("timeout").value),options:ollamaOptions(model,temperature,numPredict)};
  const r=await fetch(endpoint,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(requestBody),signal});
  if(!r.ok){let data={};try{data=await r.json();}catch{}throw new Error(data.error||"The local model could not generate the prompt.");}
  if(!r.body)throw new Error("This browser could not read Ollama's streamed response.");
  const reader=r.body.getReader(),decoder=new TextDecoder();let buffer="",response="",final={},thinkingChunks=0;
  const consume=item=>{if(item.error)throw new Error(item.error);if(item.thinking){thinkingChunks++;onUpdate?.(response,{thinking:true,thinkingChunks});}if(item.response){response+=item.response;onUpdate?.(response,{thinking:false,thinkingChunks});}if(item.done)final=item;};
  while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});const lines=buffer.split("\n");buffer=done?"":lines.pop();for(const line of lines){if(!line.trim())continue;consume(JSON.parse(line));}if(done)break;}
  if(buffer.trim())consume(JSON.parse(buffer));
  return{...final,response,thinking_chunks:thinkingChunks};
}
function openImagePromptDialog(){
  if(!$("promptOutput").value.trim())return show("Generate a video prompt first.",false);
  if(!$("imagePromptDialog").open)$("imagePromptDialog").showModal();
}
function resetImagePrompt(){
  state.imageController?.abort();state.imageMode="";$("imagePromptOutput").value="";$("imagePromptMessage").textContent="Choose Starting Image or Reference Image.";$("imagePromptMessage").className="";$("generateImageButton").disabled=true;$("copyImagePrompt").disabled=true;$("regenerateImagePrompt").disabled=true;$("applyReferenceGuidance").hidden=true;$("applyReferenceGuidance").disabled=false;$("applyReferenceGuidance").textContent="Update video prompt to use this reference image";document.querySelectorAll("[data-image-mode]").forEach(button=>button.classList.remove("active"));
}
function selectImageMode(mode){
  if(!["start","reference"].includes(mode)||state.imageController)return;
  state.imageMode=mode;$("imagePromptOutput").value="";$("generateImageButton").disabled=false;$("copyImagePrompt").disabled=true;$("regenerateImagePrompt").disabled=true;$("applyReferenceGuidance").hidden=true;$("applyReferenceGuidance").disabled=false;$("applyReferenceGuidance").textContent="Update video prompt to use this reference image";document.querySelectorAll("[data-image-mode]").forEach(button=>button.classList.toggle("active",button.dataset.imageMode===mode));$("imagePromptMessage").textContent=`${mode==="start"?"Starting Image":"Reference Image"} selected. Click Generate Image when you are ready.`;$("imagePromptMessage").className="";
}
function closeImagePromptDialog(){state.imageController?.abort();$("imagePromptDialog").close();}
function imagePromptInstruction(mode,v,videoPrompt){
  const common=`The source below is a finished video-generation prompt. Convert it into ONE standalone prompt for a generic still-image generator. Output only the image prompt with no title, heading, explanation, bullet list, model name or negative-prompt section. The selected visual style is mandatory and overrides any contradictory wording in the source: ${v.style}. Apply this exact art direction: ${styleProfile(v.style)} Preserve the exact medium, rendering method, materials, palette, lighting and camera language as well as subject identities, wardrobe, environment and continuity. Never flatten the selection into generic cinematic realism or silently substitute a different visual medium. Do not invent logos, captions, watermarks or readable text.`;
  const gameplayStill=isFirstPersonGameplay(v)?`This image must unmistakably look like a polished high-end real-time 3D first-person game screenshot captured directly through the player's eyes. Never show the player's face, body, silhouette or an outside view of the protagonist. Only the player's consistent hands, forearms, sleeves and held equipment may appear along the lower edges of frame. A restrained nonverbal HUD frame or icons may appear, but no readable text. No third-person, over-the-shoulder, external camera, live-action photograph, movie still, storybook illustration or painted concept-art composition.`:"";
  if(mode==="start")return `${common}

Create the exact fully assembled opening frame from which this animation should begin. Use the earliest stable instant of SHOT 1, immediately before its first significant movement or transformation. Describe one still composition in ${v.ratio}: subject placement, pose, expression, wardrobe, foreground, background, spatial geography, lighting direction, color palette, materials, lens perspective, depth of field and atmosphere. Make it visually compelling but do not reveal later actions, transformations, damage, arrivals or outcomes. Do not describe camera movement, editing, sound, timestamps or multiple frames. Write one polished paragraph of roughly 140–220 words.

${gameplayStill}

FINISHED VIDEO PROMPT
${videoPrompt}`;
  return `${common}

Create one strict wide 16:9 VISUAL REFERENCE SHEET—not a cinematic scene, storyboard or opening frame. First identify only the essential recurring continuity anchors, then choose 3–6 panels according to what this particular story genuinely needs. Do not force three panels and do not create panels for incidental objects. Use THREE VERTICAL PANELS for 3 anchors, a STRICT 2x2 GRID for 4, a FIVE-PANEL GRID with one wide environment panel across the top and four isolated design panels below for 5, or a STRICT 2x3 GRID for 6. Describe every panel by position. Every character, creature, object or alternate form must be isolated inside its own panel on a simple neutral studio-style background, shown in a stable full-body or three-quarter design view without performing story action. Reserve one panel for the environment alone—with no characters, creatures or foreground action. Use strong visible borders and clean neutral gutters. Absolutely no hero composition, no continuous landscape spanning multiple panels, no interaction between panels, no sequential action, no cinematic moment, and no written labels inside the image. The image prompt must begin with the chosen layout before describing any subject.

FINISHED VIDEO PROMPT
${videoPrompt}`;
}
function referenceBoardPlanPrompt(v,videoPrompt){return `Extract the essential visual continuity anchors needed to make one reference sheet for this video. Return only valid JSON. Choose between 3 and 6 anchors according to the actual story; do not force a fixed count and do not include incidental objects. Give separate anchors to visually distinct transformation states when continuity requires them. Include exactly one environment anchor describing only the empty setting, geography, architecture, terrain, vegetation and lighting—no characters or action.

For every non-environment anchor, describe a stable isolated design view suitable for a neutral-background character, creature, prop, vehicle or structure reference panel. Do not describe story action, a hero scene, an opening frame, camera movement or interaction between subjects.

${isFirstPersonGameplay(v)?`STRICT FIRST-PERSON GAMEPLAY REFERENCE RULE: Do not create a visible full-body or portrait panel for the player/protagonist. Represent the player only with one “Player POV hands and equipment” anchor describing the exact hands, forearms, sleeves, gloves, held item and restrained HUD-edge design visible through the player's eyes. Include each enemy or unchanged subject only once; different poses, emotions or actions are not separate designs. Prioritize the player-view kit, distinct enemy or creature designs, essential interactive equipment, the empty gameplay environment and a HUD language panel only when useful. Use polished high-end real-time 3D game rendering, never live-action photography or storybook/concept-art illustration.`:""}

SELECTED VISUAL STYLE
${selectedStyleLock(v)}

FINISHED VIDEO PROMPT
${videoPrompt}`;}
function referenceBoardSchema(){return{type:"object",additionalProperties:false,required:["anchors"],properties:{anchors:{type:"array",minItems:3,maxItems:6,items:{type:"object",additionalProperties:false,required:["name","type","description"],properties:{name:{type:"string"},type:{type:"string",enum:["character","creature","alternate form","prop","vehicle","structure","environment"]},description:{type:"string"}}}}}};}
function parseReferenceBoardPlan(raw){try{const text=String(raw||"").replace(/^```(?:json)?\s*/i,"").replace(/\s*```$/i,"").trim(),start=text.indexOf("{"),end=text.lastIndexOf("}");return start>=0&&end>start?JSON.parse(text.slice(start,end+1)):null;}catch{return null;}}
function normalizeReferenceBoardPlan(plan,v){
  if(!plan||!Array.isArray(plan.anchors))return null;let anchors=plan.anchors.map(anchor=>({name:String(anchor?.name||"").trim(),type:String(anchor?.type||"").trim().toLowerCase(),description:String(anchor?.description||"").trim()})).filter(anchor=>anchor.name&&anchor.description.length>=20).slice(0,6);if(anchors.length<3)return null;
  if(isFirstPersonGameplay(v)){
    const openingSubject=(String(v?.idea||"").match(/^\s*(?:a|an|the)\s+(?:(?:young|old|elderly|little|teenage)\s+)*([a-z][a-z-]*)/i)||[])[1]||"",subjectPattern=openingSubject?new RegExp(`\\b${openingSubject.replace(/[.*+?^${}()|[\]\\]/g,"\\$&")}\\b`,"i"):null,pov={name:"Player POV hands and equipment",type:"prop",description:"First-person player-view kit only: consistent hands, forearms, sleeves, gloves, held equipment and restrained nonverbal HUD-edge styling as seen through the player's eyes; never show the player's face, body, silhouette or third-person character view."};
    const isPlayerAnchor=anchor=>anchor.type==="character"&&(/(?:player|protagonist|first-person)/i.test(`${anchor.name} ${anchor.description}`)||subjectPattern?.test(`${anchor.name} ${anchor.description}`));
    const playerIndexes=anchors.map((anchor,index)=>isPlayerAnchor(anchor)?index:-1).filter(index=>index>=0),existingPov=anchors.findIndex(anchor=>/(?:player POV|hands and equipment|forearms)/i.test(`${anchor.name} ${anchor.description}`));
    if(playerIndexes.length){const first=playerIndexes[0];anchors=anchors.filter((anchor,index)=>!playerIndexes.includes(index)||index===first);anchors[first]=pov;}
    else if(existingPov<0){const envIndex=anchors.findIndex(anchor=>anchor.type==="environment");if(anchors.length<6)anchors.splice(envIndex>=0?envIndex:anchors.length,0,pov);else anchors[Math.max(0,(envIndex>=0?envIndex:anchors.length)-1)]=pov;}
  }
  const environments=anchors.filter(anchor=>anchor.type==="environment");if(!environments.length){if(anchors.length===6)anchors=anchors.slice(0,5);anchors.push({name:"Defining environment",type:"environment",description:"The empty established location with its exact geography, architecture, terrain, vegetation, weather, color palette and lighting direction; no characters, creatures or foreground action."});}else if(environments.length>1){let kept=false;anchors=anchors.filter(anchor=>anchor.type!=="environment"||!kept&&(kept=true));}
  return anchors.length>=3?anchors:null;
}
function renderReferenceBoardPrompt(v,anchors){
  const count=anchors.length,environment=anchors.find(anchor=>anchor.type==="environment"),designs=anchors.filter(anchor=>anchor!==environment),ordered=count===5?[environment,...designs]:[...designs,environment].filter(Boolean);
  const layouts={3:["THREE VERTICAL PANELS","left panel","center panel","right panel"],4:["STRICT 2x2 GRID","top-left panel","top-right panel","bottom-left panel","bottom-right panel"],5:["FIVE-PANEL GRID: one wide environment panel across the top and four equal isolated design panels below","wide top environment panel","middle-left design panel","middle-right design panel","bottom-left design panel","bottom-right design panel"],6:["STRICT 2x3 GRID","top-left panel","top-center panel","top-right panel","bottom-left panel","bottom-center panel","bottom-right panel"]},layout=layouts[count]||layouts[6],parts=ordered.map((anchor,index)=>`${layout[index+1]}: ${anchor.name}, ${anchor.description}`);
  const gameplayRule=isFirstPersonGameplay(v)?"Polished high-end real-time 3D game rendering with physically based materials and game-authentic lighting, not live-action photography or painted concept art. The player-view panel contains only hands, forearms, sleeves, held equipment and restrained nonverbal HUD-edge styling—never a portrait, full-body player or third-person protagonist. Other creatures, enemies, props and structures use isolated neutral-background design views.":"Every non-environment subject is isolated on a simple neutral studio-style background in a stable full-body or three-quarter design view with no story action.";
  return `${layout[0]}, wide 16:9 visual reference sheet. ${imageStyleLock(v)} Strong visible panel borders and clean neutral gutters make every panel unmistakably separate. ${parts.join(". ")}. ${gameplayRule} The environment panel is an empty location reference with no characters, creatures or foreground action. No hero composition, no continuous scene spanning panels, no opening frame, no interaction between panels, no sequential action, no split-screen storytelling, no labels, captions, typography, logos or watermarks.`.slice(0,4000);
}
function cleanImagePrompt(s){return String(s||"").replace(/^```(?:\w+)?\s*/i,"").replace(/\s*```$/i,"").replace(/^\s*(?:#{1,4}\s*)?(?:\*\*|__)?\s*(?:(?:Starting|Start)\s+(?:Image|Frame)|Reference\s+(?:Image|Board)|Image)\s+Prompt\s*(?:\*\*|__)?\s*:?\s*(?:\*\*|__)?\s*/i,"").replace(/^\s*(?:Here(?:'s| is)|Below is)\s+(?:the|your|a)\s+(?:image|starting image|reference image)\s+prompt\s*:?\s*/i,"").trim().slice(0,4000);}
function setImagePromptBusy(on,label="Generating image prompt…"){
  $("imagePromptStatus").hidden=!on;$("imagePromptStatusText").textContent=label;$("generateImageButton").disabled=on||!state.imageMode;$("copyImagePrompt").disabled=on||!$("imagePromptOutput").value.trim();$("regenerateImagePrompt").disabled=on||!state.imageMode;
  document.querySelectorAll("[data-image-mode]").forEach(button=>button.disabled=on);
  if(on){state.imageStarted=Date.now();clearInterval(state.imageTimer);state.imageTimer=setInterval(()=>{const s=Math.floor((Date.now()-state.imageStarted)/1000);$("imagePromptStatusText").textContent=`Generating ${state.imageMode==="start"?"starting image":"reference image"} prompt… ${Math.floor(s/60)}:${String(s%60).padStart(2,"0")}`;},250);}else{clearInterval(state.imageTimer);state.imageTimer=null;state.imageController=null;}
}
async function generateImagePrompt(mode){
  if(!["start","reference"].includes(mode)||!$("promptOutput").value.trim())return;
  openImagePromptDialog();state.imageMode=mode;state.imageAbortReason="";
  $("applyReferenceGuidance").hidden=true;$("applyReferenceGuidance").disabled=false;$("applyReferenceGuidance").textContent="Update video prompt to use this reference image";
  document.querySelectorAll("[data-image-mode]").forEach(button=>button.classList.toggle("active",button.dataset.imageMode===mode));
  const model=$("ollamaModel").value;
  if(!model){$("imagePromptMessage").textContent="Choose and load an Ollama model in Settings first.";$("imagePromptMessage").className="error";return;}
  let hardTimer=null;
  try{
    $("imagePromptMessage").textContent=`Preparing ${model}…`;$("imagePromptMessage").className="";
    if(!(await preloadModel(model)))return;
    await requireGpuResident(model);
    state.imageController=new AbortController();setImagePromptBusy(true);
    hardTimer=setTimeout(()=>{state.imageAbortReason="timeout";state.imageController?.abort();},Number($("timeout").value)*1000);
    $("imagePromptOutput").value="";
    const v=values(),videoPrompt=$("promptOutput").value.trim();let result="",rate=0;
    if(mode==="reference"){
      const raw=await requestOllama(model,referenceBoardPlanPrompt(v,videoPrompt),.68,520,state.imageController.signal,referenceBoardSchema()),anchors=normalizeReferenceBoardPlan(parseReferenceBoardPlan(raw),v);
      if(!anchors)throw new Error("Ollama did not return enough usable reference elements. Try generating the reference image prompt again.");
      result=enforceImageStyle(v,renderReferenceBoardPrompt(v,anchors));$("imagePromptOutput").value=result;
    }else{
      const streamed=await requestOllamaStream(model,imagePromptInstruction(mode,v,videoPrompt),.72,300,state.imageController.signal,partial=>{$("imagePromptOutput").value=cleanImagePrompt(partial);$("imagePromptOutput").scrollTop=$("imagePromptOutput").scrollHeight;});
      result=enforceImageStyle(v,cleanImagePrompt(streamed.response));rate=streamed.eval_count&&streamed.eval_duration?streamed.eval_count/(streamed.eval_duration/1e9):0;
    }
    if(!result)throw new Error("Ollama returned an empty image prompt.");
    $("imagePromptOutput").value=result;
    $("applyReferenceGuidance").hidden=mode!=="reference";
    $("imagePromptMessage").textContent=`${mode==="start"?"Starting image":"Reference image"} prompt ready${rate?` · ${rate.toFixed(1)} tokens/sec`:""}.`;$("imagePromptMessage").className="good";
  }catch(e){$("imagePromptMessage").textContent=e.name==="AbortError"?(state.imageAbortReason==="timeout"?"Image prompt generation reached the selected timeout.":"Image prompt generation cancelled."):e.message;$("imagePromptMessage").className="error";}
  finally{clearTimeout(hardTimer);state.imageAbortReason="";setImagePromptBusy(false);}
}
async function copyImagePrompt(){
  const output=$("imagePromptOutput");if(!output.value.trim())return;
  const copied=await copyText(output.value,output);$("imagePromptMessage").textContent=copied?"Image prompt copied to clipboard.":"The image prompt is selected. Copy it manually.";$("imagePromptMessage").className=copied?"good":"error";if(!copied){output.focus();output.select();}
}
const REFERENCE_IMAGE_GUIDE="REFERENCE IMAGE GUIDE: The supplied image is a multi-panel visual design board created for this video. Treat every character, creature, alternate form or transformation state, costume, prop, vehicle, structure, landscape element and lighting design actually depicted in its separate panels as an independent identity anchor, and match the corresponding design whenever that element appears. Do not reproduce the board itself, its panels, gutters, neutral presentation background or contact-sheet composition in the video. Do not treat it as a storyboard or starting frame. The original shot timing, framing, action, camera direction, transitions and sound instructions remain authoritative.";
const REFERENCE_LOCK_VALUE="all characters, creatures, alternate forms, costumes, props, vehicles, structures, environment and lighting shown in the generated multi-panel reference board; use its panels only as independent design anchors, never as a storyboard, starting frame or split-screen layout";
function insertReferenceGuidance(prompt){
  const text=String(prompt||"").trim(),existing=/\n{2}REFERENCE IMAGE GUIDE:[\s\S]*?(?=\n{2}(?:REFERENCE LOCK|DIALOGUE LOCK|FINAL QUALITY LOCK):|$)/i;
  if(existing.test(text))return text.replace(existing,`\n\n${REFERENCE_IMAGE_GUIDE}`);
  const marker=text.search(/\n{2}FINAL QUALITY LOCK:/i);
  return marker>=0?`${text.slice(0,marker)}\n\n${REFERENCE_IMAGE_GUIDE}${text.slice(marker)}`:`${text}\n\n${REFERENCE_IMAGE_GUIDE}`;
}
function applyReferenceGuidance(){
  if(state.imageMode!=="reference"||!$("imagePromptOutput").value.trim()||!$("promptOutput").value.trim())return;
  const output=$("promptOutput"),updated=insertReferenceGuidance(output.value),guideStart=updated.indexOf("REFERENCE IMAGE GUIDE:");output.value=updated;$("referenceEnabled").checked=true;$("referenceField").hidden=false;$("referenceLock").value=REFERENCE_LOCK_VALUE;update();
  $("applyReferenceGuidance").disabled=true;$("applyReferenceGuidance").textContent="Reference guidance added to video prompt";$("imagePromptMessage").textContent="The complete updated video prompt is ready in the main prompt box.";$("imagePromptMessage").className="good";$("imagePromptDialog").close();show("Updated video prompt ready to copy. The new reference-image guide is highlighted.",true);
  setTimeout(()=>{output.scrollIntoView({behavior:"smooth",block:"center"});output.focus({preventScroll:true});if(guideStart>=0)output.setSelectionRange(guideStart,Math.min(updated.length,guideStart+REFERENCE_IMAGE_GUIDE.length));},80);
}
function repairTimelinePrompt(v,draft){
  const dialogueRepair=dialogueRequested(v)?"The user requires dialogue. Replace every dialogue placeholder with natural, specific words in quotation marks inside the appropriate timed shot, identify the visible speaker, preserve all requested people, and give at least two actual spoken lines. Phrases such as ‘talks about,’ ‘explains,’ or ‘voiceover continues’ are invalid.":"";
  return `Rewrite the draft below as a finished MiniMax Hailuo H3 prompt. Output only the corrected prompt. Begin with an OVERALL LOOK AND CONTINUITY paragraph, reproduce and fully complete every mandatory time-coded line exactly as supplied, then end with a FINAL QUALITY LOCK. Do not omit, merge, rename or alter a time range. Give every segment a distinct visible event, framing, camera movement, transition, physical action, performance, lighting and synchronized audio. Preserve the user's idea, every requested person and the selected visual style. Remove generic advice and any living filmmaker's name. ${dialogueRepair}

MANDATORY TIMELINE
${timelineTemplate(v)}

USER SETTINGS
${JSON.stringify({...v,inferredShotFlow:effectiveFlow(v),visualArtDirection:styleProfile(v.style),cameraDirection:v.camera==="Automatic cinematic camera"?automaticCamera(v):v.camera,editingGrammar:flowProfile(effectiveFlow(v),v.duration)},null,2)}

INVALID DRAFT TO RESTRUCTURE
${draft}`;
}
function parseTimeline(result){
  const ranges=[],re=/(\d+):(\d+(?:\.\d+)?)\s*[–—-]\s*(\d+):(\d+(?:\.\d+)?)/g;let m;
  while((m=re.exec(result)))ranges.push([Number(m[1])*60+Number(m[2]),Number(m[3])*60+Number(m[4])]);
  return ranges;
}
function hasCompleteTimeline(v,result){
  const ranges=parseTimeline(result),expected=timelineRanges(v);if(ranges.length!==expected.length)return false;
  if(Math.abs(ranges[0][0])>.15||Math.abs(ranges.at(-1)[1]-v.duration)>.2)return false;
  for(let i=0;i<ranges.length;i++){
    if(ranges[i][1]<=ranges[i][0]||Math.abs(ranges[i][0]-expected[i][0])>.2||Math.abs(ranges[i][1]-expected[i][1])>.2)return false;
    if(i&&Math.abs(ranges[i][0]-ranges[i-1][1])>.15)return false;
  }
  return true;
}
function isStoryExpansion(v,result){
  if(!result||result.length<Math.max(500,v.idea.length*3)||!hasCompleteTimeline(v,result))return false;
  if(/defines the subject, environment and immediate everyday goal|captures the first decisive action|reveals a concrete consequence|resolves the idea on a memorable expression|voiceover continues to explain|talks? about (?:the process|why)|full dialogue (?:here|continues)/i.test(result))return false;
  const instructionCount=(result.match(/\b(?:you should|let the model|please add|make sure to|consider using)\b/gi)||[]).length;
  const camera=(result.match(/\b(?:camera|shot|close-up|wide|tracking|push-in|handheld|point-of-view|POV|lens|rack focus|crane|dolly|pan|tilt)\b/gi)||[]).length;
  const audio=(result.match(/\b(?:sound|audio|stereo|ambience|breath|voice|music|silence|rumble|impact)\b/gi)||[]).length;
  if(dialogueRequested(v)&&spokenLineCount(result)<2)return false;
  if(!preservesRequestedPeople(v,result))return false;
  return instructionCount<3&&camera>=Math.max(3,shotCount(v)-2)&&(!v.soundEnabled||audio>=1);
}
function dialogueRequested(v){return Boolean(v.dialogue)||/^(?:Stand-up comedy|TV comedy|Sitcom)$/.test(v.style)||/\b(?:full dialogue|dialogue|speaks?|talks?|says?|asks?|replies?|answers?|tells?|shouts?|yells?|whispers?|voiceover|take turns explaining|orders?|ordering|barista|waiter|waitress|cashier|conversation|interview|argues?|explains?)\b/i.test(v.idea);}
function spokenLineCount(result){return (String(result||"").match(/[“"][^”"\n]{2,160}[”"]/g)||[]).length;}
function preservesRequestedPeople(v,result){
  const timeline=String(result||"").slice(Math.max(0,String(result||"").search(/(?:SHOT|BEAT)\s*1\b/i))).toLowerCase(),idea=v.idea.toLowerCase();
  if(/\b(?:a|the) man\b/.test(idea)&&!/(?:\bman\b|\bmale presenter\b)/.test(timeline))return false;
  if(/\b(?:a|the) woman\b/.test(idea)&&!/(?:\bwoman\b|\bfemale presenter\b)/.test(timeline))return false;
  return true;
}
function clean(s){return String(s||"").replace(/^```(?:\w+)?\s*/i,"").replace(/\s*```$/i,"").replace(/^\s*(?:Here(?:'s| is) (?:the completed prompt based on your input|your prompt|the prompt):?|Final prompt:)\s*/i,"").replace(/^\s*(?:\*\*|__)?\s*(?:MiniMax\s+)?Hailuo\s+H3(?:\s+Video)?\s+Prompt\s*(?:\*\*|__)?\s*:?\s*(?:\*\*|__)?\s*/i,"").replace(/\n+(?:I hope this helps|Let me know[^\n]*)\.?\s*$/i,"").trim().slice(0,12000);}
function setGenerating(on){
  $("generationStatus").hidden=!on;$("generateButton").disabled=on||state.versionMismatch;$("regenerateButton").disabled=on||state.versionMismatch;if(on){$("generationLabel").textContent="Sending your idea to Ollama…";$("elapsed").textContent="0:00";state.started=Date.now();state.timer=setInterval(()=>{const s=Math.floor((Date.now()-state.started)/1000);$("elapsed").textContent=`${Math.floor(s/60)}:${String(s%60).padStart(2,"0")}`;},250);}else{clearInterval(state.timer);state.timer=null;state.controller=null;}
}
async function testOllama(){
  const note=$("ollamaMessage");note.textContent="Testing connection…";note.className="settings-message";
  try{const r=await fetch(`/api/ollama/tags?url=${encodeURIComponent($("ollamaUrl").value)}`);const d=await r.json();if(!r.ok)throw new Error(d.error||"Connection failed");const current=$("ollamaModel").value,models=(d.models||[]).filter(m=>!/(embed|embedding)/i.test(m.name||m.model||"")).sort((a,b)=>modelScore(b)-modelScore(a));$("ollamaModel").innerHTML='<option value="">Choose a model…</option>'+models.map(m=>{const name=m.name||m.model;return `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`;}).join("");if(current&&[...$("ollamaModel").options].some(o=>o.value===current))$("ollamaModel").value=current;note.textContent=`Connected. Found ${models.length} installed writing model${models.length===1?"":"s"}. Choose one, then click Load selected model. Nothing has been loaded.`;note.classList.add("good");saveSettings();if($("ollamaModel").value)await refreshSelectedModelState();else setModelLoadUI("","No model selected","Choose a model above. It will remain unloaded until you click Load selected model.","IDLE");}catch(e){note.textContent=`${e.message}. Make sure Ollama is running.`;note.className="settings-message";}
}
function escapeHtml(s=""){return String(s).replace(/[&<>'"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));}
async function copyText(text,sourceElement){
  if(navigator.clipboard&&window.isSecureContext){
    try{await navigator.clipboard.writeText(text);return true;}catch{}
  }
  const active=document.activeElement,selectionStart=sourceElement?.selectionStart,selectionEnd=sourceElement?.selectionEnd;
  const target=sourceElement||document.createElement("textarea");
  if(!sourceElement){target.value=text;target.readOnly=true;target.setAttribute("aria-hidden","true");target.style.cssText="position:fixed;left:-9999px;top:0;opacity:0";document.body.appendChild(target);}
  target.focus();target.select();target.setSelectionRange(0,text.length);
  let copied=false;try{copied=document.execCommand("copy");}catch{}
  if(!sourceElement)target.remove();
  else if(copied&&Number.isInteger(selectionStart)&&Number.isInteger(selectionEnd))target.setSelectionRange(selectionStart,selectionEnd);
  if(copied&&active?.focus)active.focus();
  return copied;
}
async function copyPrompt(){
  const output=$("promptOutput");if(!output.value)return show("Generate a prompt first.",false);
  if(await copyText(output.value,output))show("Copied to clipboard.",true);
  else{output.focus();output.select();output.setSelectionRange(0,output.value.length);show("The prompt is selected. Press and hold it, then tap Copy.",false);}
}
function downloadPrompt(){if(!$("promptOutput").value)return show("Generate a prompt first.",false);const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([$("promptOutput").value],{type:"text/plain"}));a.download="hailuo-h3-prompt.txt";a.click();URL.revokeObjectURL(a.href);}
function show(text,good){$("message").textContent=text;$("message").style.color=good?"var(--green)":"var(--danger)";clearTimeout(show.t);show.t=setTimeout(()=>$("message").textContent="",6500);}
async function checkServer(){
  const s=$("serverStatus"),banner=$("versionMismatch");
  try{
    const r=await fetch(`/api/status?t=${Date.now()}`,{cache:"no-store"});if(!r.ok)throw 0;const data=await r.json();
    if(data.version!==APP_VERSION){
      state.versionMismatch=true;s.className="server-status offline";s.querySelector("span").textContent=`Version mismatch · page ${APP_VERSION} / server ${data.version||"unknown"}`;
      $("versionMismatchText").textContent=`This browser has Prompt Builder ${APP_VERSION}, but the running server is ${data.version||"an unknown version"}. Reload before generating so old prompt logic cannot be used.`;
      banner.hidden=false;$("generateButton").disabled=true;$("regenerateButton").disabled=true;return;
    }
    state.versionMismatch=false;banner.hidden=true;s.className="server-status online";s.querySelector("span").textContent=`Server online · v${data.version}`;
    const address=data.network_urls?.[0];
    if(data.phone_access&&address){$("networkAddress").textContent=address;$("copyNetworkAddress").disabled=false;}
    else if(data.phone_access){$("networkAddress").textContent="Phone access is on, but no private-network address was detected.";$("copyNetworkAddress").disabled=true;}
    else{$("networkAddress").textContent="Phone access is off. Run ENABLE-PHONE-ACCESS.bat to turn it on.";$("copyNetworkAddress").disabled=true;}
    if(!state.controller){$("generateButton").disabled=false;$("regenerateButton").disabled=false;}
  }catch{
    state.versionMismatch=false;banner.hidden=true;s.className="server-status offline";s.querySelector("span").textContent="Local server unavailable";$("networkAddress").textContent="Start the local server to display the phone address.";$("copyNetworkAddress").disabled=true;
    if(!state.controller){$("generateButton").disabled=false;$("regenerateButton").disabled=false;}
  }
}
async function copyNetworkAddress(){const address=$("networkAddress").textContent;if(!/^https?:\/\//.test(address))return;if(await copyText(address))show("Phone address copied.",true);else window.prompt("Copy this phone address:",address);}
async function stopServer(){if(!confirm("Stop the Hailuo H3 Prompt Builder server?"))return;try{await fetch("/api/shutdown",{method:"POST"});alert("The server is stopping. Run the START batch file to use the app again.");}catch{alert("This control is available in the local app.");}}
function saveSettings(){
  const data={version:APP_VERSION,backend:currentLlmBackend(),url:$("ollamaUrl").value,model:$("ollamaModel").value,ggufFolder:$("ggufFolder").value,ggufModel:$("ggufModel").value,llamaRunner:$("llamaRunner").value,ggufContext:$("ggufContext").value,timeout:$("timeout").value};
  localStorage.setItem("h3-simple-settings",JSON.stringify(data));
  fetch("/api/settings/llm",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({backend:data.backend,ollama_url:data.url,ollama_model:data.model,gguf_folder:data.ggufFolder,gguf_model:data.ggufModel,llama_runner:data.llamaRunner,gguf_context:data.ggufContext,timeout:data.timeout})}).catch(()=>{});
}
function restoreSettings(){try{const x=JSON.parse(localStorage.getItem("h3-simple-settings"));if(x?.backend&&[...$("llmBackend").options].some(o=>o.value===x.backend))$("llmBackend").value=x.backend;if(x?.url)$("ollamaUrl").value=x.url;if(x?.ggufFolder)$("ggufFolder").value=x.ggufFolder;if(x?.llamaRunner)$("llamaRunner").value=x.llamaRunner;if(x?.ggufContext&&[...$("ggufContext").options].some(o=>o.value===String(x.ggufContext)))$("ggufContext").value=String(x.ggufContext);if(x?.timeout&&[...$("timeout").options].some(option=>option.value===String(x.timeout)))$("timeout").value=x.timeout;if(x?.version===APP_VERSION&&x?.model){$("ollamaModel").innerHTML=`<option value="${escapeHtml(x.model)}">${escapeHtml(x.model)}</option>`;}if(x?.ggufModel){$("ggufModel").innerHTML=`<option value="${escapeHtml(x.ggufModel)}">${escapeHtml(modelDisplayName(x.ggufModel))}</option>`;$("ggufModel").value=x.ggufModel;}updateLlmBackendUI();}catch{updateLlmBackendUI();}}
async function restoreServerLlmSettings(){
  try{
    const r=await fetch("/api/settings/llm",{cache:"no-store"});
    const d=await r.json();
    if(!r.ok||!d?.settings)return;
    const x=d.settings;
    if(x.backend&&[...$("llmBackend").options].some(o=>o.value===x.backend))$("llmBackend").value=x.backend;
    if(x.ollama_url)$("ollamaUrl").value=x.ollama_url;
    if(x.ollama_model){$("ollamaModel").innerHTML=`<option value="${escapeHtml(x.ollama_model)}">${escapeHtml(x.ollama_model)}</option>`;$("ollamaModel").value=x.ollama_model;}
    if(x.gguf_folder)$("ggufFolder").value=x.gguf_folder;
    if(x.llama_runner)$("llamaRunner").value=x.llama_runner;
    if(x.gguf_context&&[...$("ggufContext").options].some(o=>o.value===String(x.gguf_context)))$("ggufContext").value=String(x.gguf_context);
    if(x.timeout&&[...$("timeout").options].some(o=>o.value===String(x.timeout)))$("timeout").value=String(x.timeout);
    if(x.gguf_model){$("ggufModel").innerHTML=`<option value="${escapeHtml(x.gguf_model)}">${escapeHtml(modelDisplayName(x.gguf_model))}</option>`;$("ggufModel").value=x.gguf_model;}
    updateLlmBackendUI();
    if(x.gguf_folder){
      const wanted=x.gguf_model||"";
      try{
        const sr=await fetch("/api/gguf/scan",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({folder:x.gguf_folder})});
        const sd=await sr.json();
        if(sr.ok&&Array.isArray(sd.models)){
          $("ggufModel").innerHTML='<option value="">Choose a GGUF model…</option>'+sd.models.map(m=>`<option value="${escapeHtml(m.path)}">${escapeHtml(m.relative)} · ${(Number(m.size||0)/1073741824).toFixed(1)} GB</option>`).join("");
          if(wanted&&[...$("ggufModel").options].some(o=>o.value===wanted))$("ggufModel").value=wanted;
        }
      }catch{}
    }
    refreshSelectedModelState();
  }catch{}
}
init();
