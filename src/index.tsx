import { useCallback, useEffect, useMemo, useState } from 'react';
import { setReact } from '@ervisio/plugin-sdk/react';
import { setSdk, getSdk, type PluginSDK } from '@ervisio/plugin-sdk';
import { Button, Badge, ConfirmDialog, Icon, Select, Checkbox, Tabs, StatCard, Skeleton, toast } from './kit';
import styleText from './style.css?inline';
import { RemoteDesktop } from './remote';

type EnvId = 'gnome' | 'plasma' | 'cinnamon' | 'xfce' | 'mate' | 'lxqt';
type Profile = 'minimal' | 'standard' | 'full' | 'custom';
type Scope = 'managed' | 'system';
type Op = 'install' | 'remove';
type EnvInfo = { id: EnvId; name: string; managed: boolean; installedByPackage: boolean; sessions: Array<{ id:string; name:string; type:string }> };
type DesktopSession = { id: string; user:string; desktop:string; type:string; state:string; remote:boolean; memoryBytes:number|null; cpuSeconds:number|null };
type Job = { id:string; status:string; operation:string; desktop:string; scope:string; created:number; packages:string[]; log:string[]; error?:string|null; started?:number; ended?:number };
type Status = { supported:boolean; reason?:string; os?:{id:string;name:string;manager:string|null}; environments?:EnvInfo[]; otherSessions?:Array<{id:string;name:string;type:string}>; displayManagers?:Record<string,{active:boolean;enabled:boolean}>; defaultTarget?:string; activeSessions?:DesktopSession[]; jobs?:Job[]; managedPackages?:Record<string,string[]> };
type Plan = { op:Op; desktop:EnvId; profile:Profile; components:string; scope:Scope; manager:string; packages:string[]; alreadyInstalled:string[]; warnings:string[]; missing:string[]; commands:string[][]; planHash:string; requiresConfirmation:boolean };
const DESKTOPS:Array<{id:EnvId; name:string; desc:string; icon:string; dm:string; gradient:string}> = [
 {id:'gnome',name:'GNOME',desc:'Elegante, moderno, Wayland-first',icon:'monitor',dm:'gdm',gradient:'gnome'},
 {id:'plasma',name:'KDE Plasma',desc:'Personalizzabile e ricco di strumenti',icon:'layout',dm:'sddm',gradient:'plasma'},
 {id:'cinnamon',name:'Cinnamon',desc:'Desktop tradizionale e intuitivo',icon:'layers',dm:'lightdm',gradient:'cinnamon'},
 {id:'xfce',name:'Xfce',desc:'Leggero e veloce',icon:'zap',dm:'lightdm',gradient:'xfce'},
 {id:'mate',name:'MATE',desc:'Classico e stabile',icon:'grid',dm:'lightdm',gradient:'mate'},
 {id:'lxqt',name:'LXQt',desc:'Essenziale, consumo ridotto',icon:'box',dm:'sddm',gradient:'lxqt'}
];
const fmt = (n:number|null) => n == null ? '—' : n < 1024**2 ? Math.round(n/1024)+' KiB' : (n/1024**3>=1 ? (n/1024**3).toFixed(2)+' GiB' : Math.round(n/1024**2)+' MiB');
function errText(e:unknown):string {return e instanceof Error?e.message:String(e)}
async function command<T>(name:string,args:string[]=[]):Promise<T> {
  const res=await getSdk().api.exec(name,args);
  let data:any;
  try { data=JSON.parse(res.stdout.trim()); } catch { throw new Error((res.stderr||res.stdout||'Risposta non valida').slice(-600)); }
  if(res.exitCode!==0 || data?.ok === false) throw new Error(data?.error || res.stderr || 'Operazione non riuscita');
  return data as T;
}
function Unsupported() {return <div className="dt-root"><div className="dt-hero dt-unsupported"><div className="dt-hero-icon"><Icon name="monitor" size={32}/></div><h1>Desktop non disponibile</h1><p>Questo plugin gestisce ambienti desktop Linux. Windows non supporta GNOME, KDE, Cinnamon o loginctl come componenti del sistema operativo.</p><div className="dt-pill">Linux only</div></div></div>}
function App() {
 const [status,setStatus]=useState<Status|null>(null);
 const [busy,setBusy]=useState(true); const [error,setError]=useState(''); const [tab,setTab]=useState('remote');
 const [selected,setSelected]=useState<EnvId>('gnome'); const [profile,setProfile]=useState<Profile>('standard');
 const [custom,setCustom]=useState<string[]>(['files','terminal','login']);
 const [scope,setScope]=useState<Scope>('managed'); const [plan,setPlan]=useState<Plan|null>(null);
 const [planning,setPlanning]=useState(false); const [applyBusy,setApplyBusy]=useState(false);
 const [confirm,setConfirm]=useState(false); const [controlPending,setControlPending]=useState<{action:string;arg:string;label:string}|null>(null);
 const refresh=useCallback(async(silent=false)=>{
   if(!silent) setBusy(true);
   try {const data=await command<Status>('desktop-status'); setStatus(data);setError('');}
   catch(e){setError(errText(e));}finally{if(!silent)setBusy(false);}
 },[]);
 useEffect(()=>{void refresh(); const id=setInterval(()=>{void refresh(true)},5000);return()=>clearInterval(id);},[refresh]);
 const env=useMemo(()=>DESKTOPS.find(x=>x.id===selected)!,[selected]);
 const installed = status?.environments?.some(x=>x.id===selected)??false;
 const jobRunning=!!status?.jobs?.some(x=>x.status==='queued'||x.status==='running');
 const prepare=async(op:Op)=>{
  setPlan(null);setPlanning(true);
  try{
   const result=await command<Plan>('desktop-plan',[op,selected,profile,custom.join(','),op==='install'?'managed':scope]);
   setPlan(result); if(!result.packages.length)toast.info('Nessuna modifica necessaria','Il sistema è già nello stato richiesto.');
  }catch(e){toast.err('Impossibile preparare il piano',errText(e));}finally{setPlanning(false);}
 };
 const apply=async()=>{
  if(!plan)return;
  setApplyBusy(true);
  try{await command('desktop-apply',[plan.op,plan.desktop,plan.profile,plan.components,plan.scope,plan.planHash]);
   toast.ok('Operazione avviata','Puoi chiudere la pagina: prosegue in background.');setPlan(null);setConfirm(false);setTab('activity');await refresh(true);
  }catch(e){toast.err('Operazione non avviata',errText(e));}finally{setApplyBusy(false);}
 };
 const doControl=async()=>{
  if(!controlPending)return;
  setApplyBusy(true);
  try{const r=await command<{message:string}>('desktop-control',[controlPending.action,controlPending.arg]);toast.ok('Operazione completata',r.message);await refresh(true);}
  catch(e){toast.err('Operazione fallita',errText(e));}finally{setApplyBusy(false);setControlPending(null)}
 };
 const ask=(action:string,arg:string,label:string)=>setControlPending({action,arg,label});
 if(getSdk().platform==='windows'||status?.supported===false) return <Unsupported/>;
 return <div className="dt-root hue-term">
  <header className="dt-heading"><div className="dt-hero-icon"><Icon name="monitor" size={24}/></div><div className="dt-heading-text"><h1>Desktop</h1><p>Installa desktop Linux e usali direttamente dal browser.</p></div><Button variant="secondary" icon="refresh" loading={busy} onClick={()=>void refresh()}>Aggiorna</Button></header>
  {error&&<div className="dt-alert"><Icon name="alert"/> {error} <Button size="sm" onClick={()=>void refresh()}>Riprova</Button></div>}
  {busy&&!status?<Skeleton lines={6}/>:<>
   <section className="dt-topstats">
    <StatCard hue="term" icon="monitor" label="Desktop rilevati" value={status?.environments?.length??0}/>
    <StatCard hue="term" icon="users" label="Sessioni attive" value={status?.activeSessions?.length??0}/>
    <StatCard hue="term" icon="server" label="Display manager attivi" value={Object.values(status?.displayManagers??{}).filter(x=>x.active).length}/>
   </section>
   <div className="dt-hostline"><span><Icon name="server" size={14}/> {status?.os?.name??'Linux'} · {status?.os?.manager?.toUpperCase()??'Gestore pacchetti non riconosciuto'}</span><span><Icon name="power" size={14}/> Boot: {status?.defaultTarget??'?'}</span></div>
   <Tabs value={tab} onChange={setTab} items={[{id:'remote',label:'Apri desktop',icon:'monitor'},{id:'environments',label:'Ambienti',icon:'layout'},{id:'sessions',label:'Sessioni',icon:'users',count:status?.activeSessions?.length??0},{id:'managers',label:'Login manager',icon:'lock'},{id:'activity',label:'Attività',icon:'clock',count:status?.jobs?.length??0}]} />
   {tab==='remote'&&<RemoteDesktop/>}
   {tab==='environments'&&<div className="dt-section">
    <div className="dt-sectionhead"><div><h2>Scegli il tuo desktop</h2><p>Installa più ambienti e scegli quale usare dalla schermata di accesso.</p></div></div>
    <div className="dt-grid">{DESKTOPS.map(e=>{
      const present=status?.environments?.find(x=>x.id===e.id);return <button key={e.id} type="button" onClick={()=>{setSelected(e.id);setPlan(null)}} className={'dt-env '+(selected===e.id?'dt-selected ':'')+'dt-'+e.gradient}>
       <div className="dt-envtop"><span className="dt-envicon"><Icon name={e.icon} size={23}/></span>{present?<span className="dt-pill dt-ok">Installato</span>:<span className="dt-pill">Disponibile</span>}</div>
       <strong>{e.name}</strong><small>{e.desc}</small><span className="dt-envfoot">{present ? (present.managed?'Installato tramite Ervisio':'Rilevato sul sistema'):'Installa in pochi passaggi'} <Icon name="right"/></span>
      </button>
    })}</div>
    {!!status?.otherSessions?.length&&<div className="dt-panel"><h3>Altri desktop rilevati</h3><p>Le sessioni rilevate nei file .desktop del sistema sono mostrate anche se non hanno un installer preconfigurato.</p><div className="dt-tags">{status.otherSessions.map(s=><span className="dt-pill" key={s.id}>{s.name} · {s.type}</span>)}</div></div>}
    <div className="dt-config"><div className="dt-sectionhead"><div><h2>Configura {env.name}</h2><p>Il sistema verifica i pacchetti disponibili prima di procedere.</p></div><span className={'dt-pill '+(installed?'dt-ok':'')}>{installed?'Presente sul server':'Non installato'}</span></div>
      <div className="dt-configgrid">
       <div className="dt-field"><Select label="Tipo di installazione" value={profile} onChange={v=>{setProfile(v as Profile);setPlan(null)}} options={[{value:'minimal',label:'Minimale (solo sessione)'},{value:'standard',label:'Standard (app di base + login)'},{value:'full',label:'Completa (+ extra)'},{value:'custom',label:'Personalizzata'}]}/><p>La sessione desktop è sempre inclusa. Il login manager si installa ma non viene attivato senza un tuo comando.</p></div>
       {profile==='custom'&&<div className="dt-field"><strong>Componenti personalizzati</strong>{[{id:'files',label:'File manager'},{id:'terminal',label:'Terminale'},{id:'login',label:'Display manager'},{id:'extras',label:'Utility extra'}].map(x=><Checkbox key={x.id} checked={custom.includes(x.id)} onChange={v=>{setCustom(old=>v?[...old,x.id]:old.filter(k=>k!==x.id));setPlan(null)}} label={x.label}/>)}</div>}
      </div>
      <div className="dt-actions"><Button variant="primary" icon="download" disabled={jobRunning||!status?.os?.manager} loading={planning} onClick={()=>void prepare('install')}>Prepara installazione</Button><Button variant="secondary" icon="trash" disabled={jobRunning||!installed} loading={planning} onClick={()=>void prepare('remove')}>Prepara disinstallazione</Button>{installed&&<span className="dt-muted">{status?.environments?.find(x=>x.id===selected)?.sessions.length??0} tipi di sessione disponibili</span>}</div>
      {installed&&<div className="dt-removemode"><Select label="Tipo di disinstallazione" value={scope} onChange={v=>{setScope(v as Scope);setPlan(null)}} options={[{value:'managed',label:'Sicura: solo pacchetti aggiunti da Ervisio'},{value:'system',label:'Avanzata: rimuovi anche il desktop preesistente'}]}/><p>Non cancelliamo file personali. La modalità avanzata richiede un'anteprima e può influire sulle sessioni attive.</p></div>}
      {plan&&<div className="dt-plan"><div className="dt-planheading"><div><h3>{plan.op==='install'?'Piano di installazione':'Piano di rimozione'}</h3><p>{plan.manager.toUpperCase()} · {plan.packages.length} pacchetti richiesti</p></div><Button variant="ghost" size="sm" onClick={()=>setPlan(null)}>Chiudi</Button></div>
       <div className="dt-tags">{plan.packages.map(x=><span className="dt-pill" key={x}>{x}</span>)}</div>
       {!!plan.warnings.length&&<div className="dt-warning">{plan.warnings.map((x,i)=><p key={i}>• {x}</p>)}</div>}
       {!!plan.commands.length&&<pre className="dt-command">{plan.commands.map(x=>x.join(' ')).join('\n')}</pre>}
       <div className="dt-planfoot"><span className="dt-muted">{plan.packages.length?'Controlla il piano prima di confermare.':'Non ci sono pacchetti da modificare.'}</span><Button variant={plan.op==='remove'?'danger':'primary'} disabled={!plan.packages.length} icon={plan.op==='remove'?'trash':'check'} onClick={()=>setConfirm(true)}>{plan.op==='install'?'Installa ora':'Conferma rimozione'}</Button></div>
      </div>}
    </div>
   </div>}
   {tab==='sessions'&&<section className="dt-section"><div className="dt-sectionhead"><div><h2>Sessioni Linux attive</h2><p>Controlla utenti, protocollo, stato e consumo risorse rilevato da systemd.</p></div></div>
    {!status?.activeSessions?.length?<div className="dt-empty"><Icon name="users" size={30}/><h3>Nessuna sessione rilevata</h3><p>Le sessioni compariranno non appena un utente effettua l'accesso.</p></div>:<div className="dt-tablewrap"><table className="dt-table"><thead><tr><th>Sessione</th><th>Tipo</th><th>Memoria</th><th>CPU totale</th><th>Stato</th><th></th></tr></thead><tbody>{status.activeSessions.map(s=><tr key={s.id}><td><strong>{s.user}</strong><small>{s.desktop||s.id}</small></td><td>{s.type||'?'}</td><td>{fmt(s.memoryBytes)}</td><td>{s.cpuSeconds==null?'—':s.cpuSeconds+' s'}</td><td><span className="dt-pill">{s.state}</span></td><td><Button size="sm" variant="danger" onClick={()=>ask('session-end',s.id,`Terminare la sessione ${s.id} di ${s.user}? Il lavoro non salvato potrebbe andare perso.`)}>Termina</Button></td></tr>)}</tbody></table></div>}
    <div className="dt-note">CPU = tempo CPU accumulato dalla scope systemd, non percentuale in tempo reale. Memoria = consumo della scope se disponibile.</div>
   </section>}
   {tab==='managers'&&<section className="dt-section"><div className="dt-sectionhead"><div><h2>Display manager</h2><p>Gestisci l'avvio grafico. Le modifiche possono interrompere sessioni attive: usa SSH per intervenire a distanza.</p></div></div>
    <div className="dt-grid">{Object.entries(status?.displayManagers??{}).map(([dm,value])=><div key={dm} className="dt-panel"><div className="dt-dmtop"><div><h3>{dm.toUpperCase()}</h3><p>{dm==='gdm'?'GNOME Display Manager':dm==='sddm'?'Simple Desktop Display Manager':dm==='lightdm'?'LightDM':'LXDM'}</p></div><span className={'dt-pill '+(value.active?'dt-ok':'')}>{value.active?'Attivo':'Fermo'}</span></div><div className="dt-row"><span className="dt-muted">Avvio automatico</span><strong>{value.enabled?'Abilitato':'Disabilitato'}</strong></div><div className="dt-actions"><Button size="sm" variant="secondary" onClick={()=>ask(value.enabled?'dm-disable':'dm-enable',dm,`${value.enabled?'Disabilitare':'Abilitare'} ${dm} all'avvio?`)}>{value.enabled?'Disabilita boot':'Abilita boot'}</Button><Button size="sm" variant="secondary" onClick={()=>ask(value.active?'dm-stop':'dm-start',dm,`${value.active?'Arrestare':'Avviare'} ${dm} ora? Potresti perdere la sessione grafica.`)}>{value.active?'Arresta':'Avvia ora'}</Button></div></div>)}</div>
    <div className="dt-panel"><h3>Target di avvio predefinito</h3><p>Questa modifica vale dal prossimo avvio e non riavvia la macchina.</p><div className="dt-actions"><Button variant="secondary" onClick={()=>ask('graphical','-','Impostare l’avvio grafico come predefinito?')}>Avvio grafico</Button><Button variant="secondary" onClick={()=>ask('text','-','Impostare l’avvio testuale come predefinito?')}>Avvio testuale</Button></div></div>
   </section>}
   {tab==='activity'&&<section className="dt-section"><div className="dt-sectionhead"><div><h2>Operazioni e attività</h2><p>Le installazioni continuano in background con systemd, anche se chiudi questa pagina.</p></div></div>
    {!status?.jobs?.length?<div className="dt-empty"><Icon name="clock" size={28}/><h3>Nessuna operazione</h3><p>Avvia l'installazione di un desktop per vedere i dettagli qui.</p></div>:status.jobs.map(j=><div className="dt-job" key={j.id}><div className="dt-jobtitle"><div><strong>{j.operation==='install'?'Installazione':'Rimozione'} {ENV_NAMES_UI(j.desktop)}</strong><small>{new Date(j.created*1000).toLocaleString('it-IT')} · {j.packages.length} pacchetti</small></div><span className={'dt-pill '+(j.status==='done'?'dt-ok':j.status==='failed'?'dt-error':'dt-working')}>{j.status}</span></div>{j.error&&<p className="dt-errorline">{j.error}</p>}{!!j.log?.length&&<pre className="dt-command dt-log">{j.log.slice(-22).join('\n')}</pre>}</div>)}
   </section>}
  </>}
  <ConfirmDialog open={confirm&&!!plan} onClose={()=>setConfirm(false)} onConfirm={()=>void apply()} title={plan?.op==='install'?'Installare il desktop?':'Rimuovere il desktop?'} description={plan?.op==='remove'?'Potresti interrompere sessioni e perdere applicazioni installate. Controlla attentamente i pacchetti indicati.':'Ervisio eseguirà solo i pacchetti indicati nell’anteprima, tramite il gestore del sistema.'} confirmText={plan?.op==='remove'?'RIMUOVI':'INSTALLA'} confirmLabel={plan?.op==='remove'?'Rimuovi':'Installa'} danger={plan?.op==='remove'} icon="alert" />
  <ConfirmDialog open={!!controlPending} onClose={()=>setControlPending(null)} onConfirm={()=>void doControl()} title="Conferma operazione" description={controlPending?.label??''} confirmText={controlPending?.action==='session-end'||controlPending?.action==='dm-stop'?'CONFERMA':undefined} confirmLabel="Conferma" danger={controlPending?.action==='session-end'||controlPending?.action==='dm-stop'} icon="alert"/>
  {applyBusy&&<div className="dt-overlay"><span className="dt-loader"/>Invio del comando…</div>}
 </div>;
}
function ENV_NAMES_UI(v:string){return DESKTOPS.find(x=>x.id===v)?.name??v}
function Widget(){const [st,setSt]=useState<Status|null>(null);useEffect(()=>{command<Status>('desktop-status').then(setSt).catch(()=>{});},[]);if(getSdk().platform==='windows')return <div className="dt-widget">Desktop · Linux only</div>;return <div className="dt-widget"><strong>Desktop</strong><span>{st?.environments?.length??'…'} ambienti · {st?.activeSessions?.length??'…'} sessioni</span></div>}
export default function activate(sdk:PluginSDK){setSdk(sdk);setReact(sdk.react);if(!document.getElementById('desktop-plugin-styles')){const style=document.createElement('style');style.id='desktop-plugin-styles';style.textContent=styleText;document.head.appendChild(style);}sdk.registerPage('desktop',App);sdk.registerWidget({id:'desktop-status',render:Widget});}
