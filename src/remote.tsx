import { useEffect, useRef, useState } from 'react';
import RFB from '@novnc/novnc';
import { getSdk } from '@ervisio/plugin-sdk';
import { Button, ConfirmDialog, Select, toast } from './kit';

type RemoteInfo = { desktop: string; geometry: string; display: string; started: number; token: string; running: boolean; memoryBytes: number; cpuPercent: number };
type RemoteState = { ok: boolean; gateway: boolean; vnc: boolean; session: RemoteInfo | null;
  available: Record<string,boolean>; unavailableReasons: Record<string,string|null>; customSessions: Record<string,string> };
type StreamHandle = { close(): void };

async function gateway<T>(method: string, path: string, body?: object): Promise<T> {
  const r = await getSdk().api.http('desktop-gateway', { method, path, ...(body ? { body } : {}) });
  if (r.status < 200 || r.status > 299) {
    let reason = r.body;
    try { reason = r.json().error || reason; } catch { /* use text */ }
    throw new Error(reason || `HTTP ${r.status}`);
  }
  return r.json() as T;
}

/** A WebSocket-compatible channel backed by Ervisio's signed, local Unix socket API.
 * noVNC accepts a channel object directly, so the plugin needs NO public VNC port.
 */
export class ErvisioRfbChannel {
  readonly protocol = '';
  binaryType = 'arraybuffer';
  readyState = 0;
  onmessage: ((e: {data: ArrayBuffer}) => void) | null = null;
  onopen: (() => void) | null = null;
  onerror: ((e: Error) => void) | null = null;
  onclose: ((e: {wasClean: boolean}) => void) | null = null;
  private stream: StreamHandle | null = null;
  private writes: Uint8Array[] = [];
  private flushing = false;

  constructor(private readonly token: string) {
    this.stream = getSdk().api.httpStream('desktop-gateway',
      { method: 'GET', path: '/v1/stream', headers: {'X-Desktop-Token': token} }, {
        onStart: status => {
          if (status !== 200) { this.fail(new Error('Desktop stream: HTTP ' + status)); return; }
          this.readyState = 1;
          this.onopen?.();
        },
        onData: chunk => {
          if (this.readyState !== 1) return;
          const copy = new Uint8Array(chunk.length);
          copy.set(chunk);
          this.onmessage?.({data: copy.buffer});
        },
        onError: err => this.fail(err),
        onEnd: () => this.close()
      });
  }

  send(value: ArrayBuffer | Uint8Array | string): void {
    if (this.readyState !== 1) return;
    const bytes = typeof value === 'string' ? new TextEncoder().encode(value) :
      value instanceof Uint8Array ? value.slice() : new Uint8Array(value.slice(0));
    this.writes.push(bytes);
    if (!this.flushing) void this.flush();
  }

  private async flush(): Promise<void> {
    this.flushing = true;
    try {
      while (this.writes.length && this.readyState === 1) {
        // Preserve protocol ordering and combine small mouse/keyboard events.
        let total = 0;
        const pending: Uint8Array[] = [];
        while (this.writes.length && total < 32 * 1024) {
          const item = this.writes.shift()!;
          pending.push(item);
          total += item.length;
        }
        const buffer = new Uint8Array(total);
        let offset = 0;
        for (const item of pending) { buffer.set(item, offset); offset += item.length; }
        const result = await getSdk().api.http('desktop-gateway', {
          method: 'POST', path: '/v1/input', headers: {'X-Desktop-Token': this.token}, body: buffer
        });
        if (result.status !== 200) throw new Error('Desktop input rejected: HTTP ' + result.status);
      }
    } catch (e) { this.fail(e instanceof Error ? e : new Error(String(e))); }
    finally { this.flushing = false; }
  }

  private fail(e: Error): void { if (this.readyState >= 2) return; this.onerror?.(e); this.close(); }
  close(): void {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.writes = [];
    this.stream?.close();
    this.stream = null;
    this.onclose?.({ wasClean: true });
  }
}

type RfbClient = RFB & { qualityLevel: number; compressionLevel: number; resizeSession: boolean;
  scaleViewport: boolean; showDotCursor: boolean; viewOnly: boolean; clipboardPasteFrom(text:string):void;
  sendCtrlAltDel():void; disconnect():void };

const DESKTOP_OPTIONS = [
  {value:'gnome', label:'GNOME (X11)'}, {value:'plasma', label:'KDE Plasma (X11)'},
  {value:'cinnamon',label:'Cinnamon'}, {value:'xfce',label:'Xfce'},
  {value:'mate',label:'MATE'}, {value:'lxqt',label:'LXQt'}
];

function Viewer({session, onDisconnect}:{session:RemoteInfo;onDisconnect():void}) {
  const target = useRef<HTMLDivElement>(null);
  const screen = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<RfbClient|null>(null);
  const [connected, setConnected] = useState(false);
  const [viewOnly, setViewOnly] = useState(false);
  const [paste,setPaste] = useState('');
  const [clip,setClip] = useState('');
  const [resolution,setResolution] = useState(false);
  useEffect(() => {
    if (!target.current) return;
    const channel = new ErvisioRfbChannel(session.token);
    const rfb = new RFB(target.current, channel as unknown as WebSocket, {shared:true}) as RfbClient;
    rfbRef.current = rfb;
    rfb.scaleViewport = true;
    rfb.resizeSession = false;
    rfb.qualityLevel = 6;
    rfb.compressionLevel = 6;
    rfb.showDotCursor = true;
    const handleConnected = () => setConnected(true);
    const handleDisconnected = () => {setConnected(false); onDisconnect();};
    const handleClipboard = (e: Event) => setClip((e as CustomEvent<{text:string}>).detail.text || '');
    rfb.addEventListener('connect', handleConnected);
    rfb.addEventListener('disconnect', handleDisconnected);
    rfb.addEventListener('clipboard', handleClipboard);
    return () => {rfb.removeEventListener('disconnect',handleDisconnected); rfb.disconnect();channel.close();rfbRef.current = null;};
  }, [session.token]);
  return <div className="dt-viewer-wrap">
    <div className="dt-viewer-toolbar">
      <span className={'dt-pill '+(connected?'dt-ok':'dt-working')}>{connected?'Connesso · mouse e tastiera attivi':'Connessione RFB…'}</span>
      <label className="dt-viewer-check"><input type="checkbox" checked={viewOnly} onChange={e=>{setViewOnly(e.target.checked);if(rfbRef.current)rfbRef.current.viewOnly=e.target.checked;}}/> Solo visualizzazione</label>
      <label className="dt-viewer-check"><input type="checkbox" checked={resolution} onChange={e=>{setResolution(e.target.checked);if(rfbRef.current)rfbRef.current.resizeSession=e.target.checked;}}/> Ridimensiona</label>
      <Button size="sm" variant="secondary" onClick={()=>rfbRef.current?.sendCtrlAltDel()}>Ctrl+Alt+Canc</Button>
      <Button size="sm" variant="secondary" onClick={()=>screen.current?.requestFullscreen().catch(()=>toast.info('Schermo intero non consentito dal browser'))}>Schermo intero</Button>
      <Button size="sm" variant="danger" onClick={onDisconnect}>Disconnetti</Button>
    </div>
    <div className="dt-viewer-screen" ref={screen}><div ref={target} className="dt-rfb" tabIndex={0} aria-label="Desktop remoto interattivo" /></div>
    <div className="dt-viewer-clipboard">
      <label>Invia testo al desktop</label>
      <input value={paste} onChange={e=>setPaste(e.target.value)} placeholder="Incolla qui il testo" />
      <Button size="sm" variant="secondary" disabled={!paste||!connected} onClick={()=>rfbRef.current?.clipboardPasteFrom(paste)}>Incolla nel desktop</Button>
      {clip && <Button size="sm" variant="secondary" onClick={()=>{setPaste(clip);toast.info('Testo ricevuto dal desktop') }}>Testo ricevuto</Button>}
    </div>
    <p className="dt-note">Sessione X11 virtuale persistente, privata per il tuo account Linux. Non è lo schermo fisico già aperto su GNOME/Wayland.</p>
  </div>;
}

export function RemoteDesktop() {
  const [state, setState] = useState<RemoteState|null>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [desktop,setDesktop]=useState('xfce');
  const [geometry,setGeometry]=useState('1280x720');
  const [viewing,setViewing]=useState(false);
  const fetchState = async () => {
    try {const next = await gateway<RemoteState>('GET','/v1/status');setState(next);setError('');}
    catch(e) {setState(null);setError(e instanceof Error?e.message:String(e));}
  };
  useEffect(()=>{void fetchState()},[]);
  const installGateway = async()=>{
    setBusy(true);
    try {
      const res=await getSdk().api.exec('desktop-gateway-install');
      const message=JSON.parse(res.stdout.trim());
      if(res.exitCode || !message.ok) throw new Error(message.error||res.stderr||'Installazione non riuscita');
      toast.ok('Gateway pronto','TigerVNC e servizio locale avviati.');
      await fetchState();
    } catch(e){toast.err('Installazione gateway fallita',e instanceof Error?e.message:String(e));}
    finally {setBusy(false);}
  };
  const start = async()=>{
    setBusy(true);
    try {
      const response=await gateway<{ok:boolean;session:RemoteInfo}>('POST','/v1/session',{desktop,geometry});
      await fetchState();setViewing(true);
      toast.ok('Desktop avviato',response.session.display+' · '+desktop);
    } catch(e) {toast.err('Impossibile avviare il desktop',e instanceof Error?e.message:String(e));}
    finally{setBusy(false);}
  };
  const stop=async()=>{
    setConfirmStop(false);
    setBusy(true);
    try {setViewing(false);await gateway('DELETE','/v1/session');await fetchState();toast.ok('Sessione terminata');}
    catch(e){toast.err('Errore',e instanceof Error?e.message:String(e));}
    finally{setBusy(false);}
  };
  if(getSdk().platform==='windows')return <div className="dt-panel">Non disponibile su Windows.</div>;
  return <section className="dt-section">
    <div className="dt-sectionhead"><div><h2>Il tuo computer nel browser</h2><p>Avvia un desktop Linux completo e controllalo con mouse, tastiera e app. La connessione passa attraverso Ervisio, senza porte VNC esposte.</p></div></div>
    {error && <div className="dt-panel dt-warning"><strong>Gateway non raggiungibile</strong><p>{error}</p><p>Il gateway è un componente separato e va installato una sola volta sul server.</p><div className="dt-actions"><Button variant="primary" loading={busy} onClick={()=>void installGateway()}>Installa gateway remoto</Button><Button variant="secondary" onClick={()=>void fetchState()}>Verifica</Button></div></div>}
    {state && <div className="dt-panel">
      <div className="dt-dmtop"><div><h3>Sessione privata</h3><p>{state.session?`${state.session.desktop} · ${state.session.geometry} · ${Math.round((state.session.memoryBytes||0)/1048576)} MiB · CPU ${state.session.cpuPercent||0}%`:'Nessuna sessione virtuale attiva'}</p></div><span className={'dt-pill '+(state.session?'dt-ok':'')}>{state.session?'In esecuzione':'Pronto'}</span></div>
      {!state.session ? <>
        <div className="dt-configgrid"><Select label="Ambiente grafico" value={desktop} onChange={setDesktop} options={[...DESKTOP_OPTIONS,...Object.entries(state.customSessions||{}).map(([value,label])=>({value,label:label+" (rilevato)"}))]}/>
        <Select label="Risoluzione iniziale" value={geometry} onChange={setGeometry} options={['1024x768','1280x720','1366x768','1600x900','1920x1080'].map(x=>({value:x,label:x}))}/></div>
        {state.unavailableReasons[desktop] && <p className="dt-note">{state.unavailableReasons[desktop]}. Installa la versione X11 dell'ambiente prima di avviarlo.</p>}
        <div className="dt-actions"><Button variant="primary" disabled={!state.available[desktop]} loading={busy} onClick={()=>void start()}>Avvia e apri desktop</Button></div>
      </> : <div className="dt-actions">
        {!viewing && <Button variant="primary" onClick={()=>setViewing(true)}>Apri desktop</Button>}
        <Button variant="danger" loading={busy} onClick={()=>setConfirmStop(true)}>Termina sessione</Button>
      </div>}
    </div>}
    {viewing && state?.session && <Viewer session={state.session} onDisconnect={()=>setViewing(false)}/>}
    <div className="dt-note">Per la prima versione il collegamento grafico usa TigerVNC con una nuova sessione X11. La condivisione della sessione fisica Wayland richiede un backend dedicato e non è ancora prevista.</div>
    <ConfirmDialog open={confirmStop} onClose={()=>setConfirmStop(false)} onConfirm={()=>void stop()} title="Terminare il desktop remoto?" description="Le applicazioni nella sessione virtuale verranno chiuse e il lavoro non salvato potrebbe andare perso." confirmLabel="Termina sessione" confirmText="TERMINA" danger icon="alert"/>
  </section>;
}
