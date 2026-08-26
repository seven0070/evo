/**
 * Aero-Industrial Instrument Panel: a quiet, asymmetric command surface with graphite materials,
 * precise mono telemetry, restrained Arc Amber readiness states, and deliberately safe browser-only actions.
 */
import { useEffect, useRef, useState } from "react";
import {
  Activity,
  Bell,
  Brain,
  Check,
  ChevronRight,
  Circle,
  Clock3,
  Command,
  FileText,
  Headphones,
  Layers3,
  ListChecks,
  Menu,
  Mic,
  MicOff,
  MonitorCog,
  MoreHorizontal,
  Play,
  Plus,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Terminal,
  UserRound,
  Volume2,
  X,
} from "lucide-react";

type NavKey = "Command" | "Memory" | "Routines" | "Devices";

type RecognitionLike = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
};

type RecognitionConstructor = new () => RecognitionLike;
type RecognitionWindow = Window &
  typeof globalThis & {
    SpeechRecognition?: RecognitionConstructor;
    webkitSpeechRecognition?: RecognitionConstructor;
  };

const navigation: { label: NavKey; icon: typeof Command }[] = [
  { label: "Command", icon: Command },
  { label: "Memory", icon: Brain },
  { label: "Routines", icon: Layers3 },
  { label: "Devices", icon: MonitorCog },
];

const initialActivity = [
  { time: "09:42", title: "Morning briefing assembled", detail: "Calendar · weather · focus blocks", tone: "active" },
  { time: "09:37", title: "Workspace scan completed", detail: "7 files observed · no changes made", tone: "quiet" },
  { time: "09:15", title: "Focus mode scheduled", detail: "10:00 → 11:30 · notifications paused", tone: "quiet" },
];

const systemNodes = [
  { label: "Voice interface", value: "Ready", status: "ready" },
  { label: "Local memory", value: "Scoped", status: "ready" },
  { label: "Permissions", value: "Review", status: "review" },
];

function ReactorDial({ listening }: { listening: boolean }) {
  return (
    <div className={`reactor ${listening ? "is-listening" : ""}`} aria-label={listening ? "Voice capture is active" : "Voice capture is inactive"}>
      <span className="reactor-track reactor-track-a" />
      <span className="reactor-track reactor-track-b" />
      <span className="reactor-track reactor-track-c" />
      <span className="reactor-core">
        <Sparkles size={16} strokeWidth={1.8} />
      </span>
      <span className="reactor-tick reactor-tick-a" />
      <span className="reactor-tick reactor-tick-b" />
      <span className="reactor-tick reactor-tick-c" />
      <span className="reactor-tick reactor-tick-d" />
    </div>
  );
}

function TelemetryLine() {
  return (
    <svg className="telemetry-line" viewBox="0 0 390 92" role="img" aria-label="System readiness telemetry remains stable">
      <defs>
        <linearGradient id="telemetryStroke" x1="0" x2="1" y1="0" y2="0">
          <stop offset="0%" stopColor="#6b7783" stopOpacity="0.2" />
          <stop offset="45%" stopColor="#ffb15e" />
          <stop offset="100%" stopColor="#ffb15e" stopOpacity="0.55" />
        </linearGradient>
      </defs>
      <path className="telemetry-grid" d="M0 18H390M0 46H390M0 74H390" />
      <path className="telemetry-area" d="M0 63 L25 59 L49 61 L73 49 L98 53 L120 44 L144 50 L169 35 L194 39 L220 28 L246 37 L270 25 L295 32 L320 21 L348 25 L370 15 L390 19 V92 H0Z" />
      <path className="telemetry-path" d="M0 63 L25 59 L49 61 L73 49 L98 53 L120 44 L144 50 L169 35 L194 39 L220 28 L246 37 L270 25 L295 32 L320 21 L348 25 L370 15 L390 19" />
      <circle className="telemetry-point" cx="270" cy="25" r="3.2" />
    </svg>
  );
}

function assistantReply(command: string) {
  const normalized = command.toLowerCase();
  if (normalized.includes("brief") || normalized.includes("morning")) {
    return "Briefing assembled. Three priority blocks are ready, and there are no unresolved system reviews.";
  }
  if (normalized.includes("focus")) {
    return "Focus routine is queued for 10:00. I will keep this browser session quiet; no device settings will be changed.";
  }
  if (normalized.includes("system") || normalized.includes("status") || normalized.includes("check")) {
    return "Surface check complete. Voice interface is ready, local memory remains scoped, and permissioned actions still require your review.";
  }
  if (normalized.includes("help") || normalized.includes("what can")) {
    return "Try a briefing, a system check, or a focus routine. This web prototype demonstrates the interaction layer and does not control your devices.";
  }
  return "Command understood. I have recorded it in this session and prepared a reviewable response. Connect a trusted service to carry out external work.";
}

export default function Home() {
  const [activeNav, setActiveNav] = useState<NavKey>("Command");
  const [command, setCommand] = useState("");
  const [lastReply, setLastReply] = useState("Your command surface is online. What should we review?");
  const [activity, setActivity] = useState(initialActivity);
  const [listening, setListening] = useState(false);
  const [voiceAvailable, setVoiceAvailable] = useState(true);
  const [compactNav, setCompactNav] = useState(false);
  const [now, setNow] = useState(new Date());
  const recognitionRef = useRef<RecognitionLike | null>(null);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const speak = (text: string) => {
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 1.03;
      utterance.pitch = 0.9;
      window.speechSynthesis.speak(utterance);
    }
  };

  const recordActivity = (title: string, detail: string) => {
    setActivity((current) => [
      { time: new Intl.DateTimeFormat([], { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date()), title, detail, tone: "active" },
      ...current.slice(0, 2),
    ]);
  };

  const executeCommand = (sourceCommand: string) => {
    const cleaned = sourceCommand.trim();
    if (!cleaned) return;
    const reply = assistantReply(cleaned);
    setLastReply(reply);
    setCommand("");
    recordActivity("Command reviewed", cleaned.length > 44 ? `${cleaned.slice(0, 44)}…` : cleaned);
    speak(reply);
  };

  const toggleListening = () => {
    if (listening) {
      recognitionRef.current?.stop();
      return;
    }

    const VoiceRecognition = (window as RecognitionWindow).SpeechRecognition || (window as RecognitionWindow).webkitSpeechRecognition;
    if (!VoiceRecognition) {
      setVoiceAvailable(false);
      setLastReply("Voice capture is not available in this browser. You can still issue a command from the input below.");
      return;
    }

    const recognition = new VoiceRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = navigator.language || "en-US";
    recognition.onresult = (event) => {
      const transcript = event.results[event.results.length - 1][0].transcript;
      setCommand(transcript);
      executeCommand(transcript);
    };
    recognition.onerror = () => {
      setLastReply("Voice capture needs browser microphone permission. Your command surface remains available for typed requests.");
      setListening(false);
    };
    recognition.onend = () => setListening(false);
    recognitionRef.current = recognition;
    recognition.start();
    setListening(true);
    setLastReply("Listening. Speak a short request when you are ready.");
  };

  const runCheck = () => {
    const reply = assistantReply("run system check");
    setLastReply(reply);
    recordActivity("System check completed", "Browser-safe diagnostic · no device actions");
    speak(reply);
  };

  const usePrompt = (prompt: string) => {
    setCommand(prompt);
    window.setTimeout(() => executeCommand(prompt), 70);
  };

  return (
    <div className="app-shell">
      <aside className={`ops-rail ${compactNav ? "is-compact" : ""}`} aria-label="Primary navigation">
        <div className="rail-top">
          <button className="brand-lockup" onClick={() => setActiveNav("Command")} aria-label="Return to command center">
            <img src="/assets/evo-aperture-mark.png" alt="" className="brand-mark" />
            {!compactNav && <span className="brand-word">EVO</span>}
          </button>
          <button className="icon-button rail-toggle" onClick={() => setCompactNav((value) => !value)} aria-label="Toggle navigation width">
            {compactNav ? <Menu size={18} /> : <X size={18} />}
          </button>
        </div>

        <div className="rail-label">{!compactNav && "Operations"}</div>
        <nav className="nav-stack">
          {navigation.map(({ label, icon: Icon }) => (
            <button
              key={label}
              className={`nav-item ${activeNav === label ? "is-active" : ""}`}
              onClick={() => {
                setActiveNav(label);
                setLastReply(`${label} view selected. This interface is ready to extend with your trusted services.`);
              }}
            >
              <Icon size={18} strokeWidth={1.8} />
              {!compactNav && <span>{label}</span>}
              {!compactNav && activeNav === label && <ChevronRight size={15} className="nav-chevron" />}
            </button>
          ))}
        </nav>

        <div className="rail-spacer" />
        <button className="profile-block" onClick={() => setLastReply("Profile controls are reserved for a connected account.")}>
          <span className="profile-avatar"><UserRound size={17} /></span>
          {!compactNav && <span className="profile-copy"><strong>Operator</strong><small>Local session</small></span>}
          {!compactNav && <MoreHorizontal size={17} />}
        </button>
      </aside>

      <main className="command-workspace">
        <header className="topbar">
          <div className="topbar-title">
            <span className="eyebrow"><span className="status-dot" /> Browser assistant</span>
            <h1>{activeNav === "Command" ? "Command center" : activeNav}</h1>
          </div>
          <div className="topbar-actions">
            <button className="icon-button" onClick={() => setLastReply("Notifications are quiet. No new approval requests.")} aria-label="Open notifications"><Bell size={18} /></button>
            <button className="icon-button" onClick={() => setLastReply("Settings are part of the connected-agent setup. This prototype maintains local presentation preferences only.")} aria-label="Open settings"><Settings2 size={18} /></button>
            <div className="time-readout"><Clock3 size={15} /><span>{new Intl.DateTimeFormat([], { hour: "2-digit", minute: "2-digit", hour12: false }).format(now)}</span></div>
          </div>
        </header>

        <section className="mission-bar">
          <div className="mission-icon"><Activity size={18} /></div>
          <div>
            <span className="mono-label">Session protocol</span>
            <p><strong>Human-controlled.</strong> Commands stay reviewable; sensitive actions are never assumed.</p>
          </div>
          <button className="text-action" onClick={runCheck}>Run check <ChevronRight size={15} /></button>
        </section>

        <div className="dashboard-grid">
          <section className="assistant-column">
            <article className="core-panel">
              <img className="core-image" src="/assets/evo-reactor-hero.jpg" alt="" />
              <div className="core-scrim" />
              <div className="core-index">01 / ASSISTANT CORE</div>
              <div className="core-content">
                <div className="core-copy">
                  <span className="mono-label">{listening ? "Voice channel active" : "Standing by"}</span>
                  <h2>Good {now.getHours() < 12 ? "morning" : now.getHours() < 18 ? "afternoon" : "evening"}, Operator.</h2>
                  <p>{lastReply}</p>
                </div>
                <div className="reactor-wrap">
                  <ReactorDial listening={listening} />
                  <span className="reactor-caption">{listening ? "Listening" : "EVO // READY"}</span>
                </div>
              </div>
              <div className="core-footer">
                <span><ShieldCheck size={15} /> Permission-aware</span>
                <span><Circle size={6} fill="currentColor" /> Browser session</span>
              </div>
            </article>

            <article className="command-panel">
              <div className="panel-heading">
                <div><span className="mono-label">Direct input</span><h3>Give EVO a task</h3></div>
                <kbd><Command size={13} /> K</kbd>
              </div>
              <form onSubmit={(event) => { event.preventDefault(); executeCommand(command); }} className="command-form">
                <Search size={19} className="command-search" />
                <input
                  value={command}
                  onChange={(event) => setCommand(event.target.value)}
                  placeholder="Ask for a briefing, status check, or focus routine…"
                  aria-label="Assistant command"
                />
                <button type="button" className={`mic-button ${listening ? "is-live" : ""}`} onClick={toggleListening} aria-label={listening ? "Stop voice capture" : "Start voice capture"}>
                  {listening ? <MicOff size={18} /> : <Mic size={18} />}
                </button>
                <button type="submit" className="send-button" aria-label="Send command"><Send size={17} /></button>
              </form>
              <div className="suggestion-row">
                <span>Try</span>
                {['Prepare my morning briefing', 'Run a system check', 'Start a focus routine'].map((prompt) => (
                  <button key={prompt} onClick={() => usePrompt(prompt)}>{prompt}</button>
                ))}
              </div>
              {!voiceAvailable && <p className="voice-note"><Headphones size={14} /> Voice capture is unavailable here; typed commands still work.</p>}
            </article>

            <article className="activity-panel">
              <div className="panel-heading">
                <div><span className="mono-label">Session record</span><h3>Recent activity</h3></div>
                <button className="quiet-button" onClick={() => setLastReply("The full session history will be available when persistent memory is connected.")}>View all</button>
              </div>
              <div className="activity-list">
                {activity.map((item, index) => (
                  <div className="activity-row" key={`${item.time}-${item.title}-${index}`}>
                    <span className={`activity-marker ${item.tone === "active" ? "is-active" : ""}`}><Check size={12} /></span>
                    <time>{item.time}</time>
                    <div><strong>{item.title}</strong><span>{item.detail}</span></div>
                    <ChevronRight size={16} />
                  </div>
                ))}
              </div>
            </article>
          </section>

          <aside className="diagnostic-column" aria-label="System diagnostics">
            <article className="status-panel">
              <div className="panel-heading">
                <div><span className="mono-label">System health</span><h3>All systems nominal</h3></div>
                <span className="health-badge">98%</span>
              </div>
              <TelemetryLine />
              <div className="telemetry-labels"><span>09:00</span><span>09:20</span><span>09:40</span><span>Now</span></div>
            </article>

            <article className="nodes-panel">
              <div className="panel-heading"><div><span className="mono-label">Node status</span><h3>Connected surfaces</h3></div><button className="quiet-icon" onClick={() => setLastReply("Connection management is available once you link a trusted service.")}><Plus size={17} /></button></div>
              <div className="node-list">
                {systemNodes.map((node) => (
                  <div className="node-row" key={node.label}>
                    <span className={`node-glyph ${node.status === "review" ? "is-review" : ""}`}><Terminal size={15} /></span>
                    <span><strong>{node.label}</strong><small>{node.status === "review" ? "Approval required" : "Local browser"}</small></span>
                    <em className={node.status === "review" ? "review" : "ready"}>{node.value}</em>
                  </div>
                ))}
              </div>
            </article>

            <article className="focus-panel">
              <img className="focus-image" src="/assets/evo-diagnostic-atmosphere.jpg" alt="" />
              <div className="focus-shade" />
              <div className="focus-content">
                <span className="mono-label">Next routine</span>
                <h3>Deep focus</h3>
                <p>90 minutes · quiet browser session</p>
                <button onClick={() => usePrompt("Start a focus routine")}><Play size={14} fill="currentColor" /> Queue routine</button>
              </div>
            </article>

            <article className="security-note">
              <div className="security-icon"><ShieldCheck size={17} /></div>
              <div><strong>Review before executing.</strong><p>This prototype demonstrates a safe browser surface; it never takes external action by itself.</p></div>
            </article>
          </aside>
        </div>

        <footer className="workspace-footer">
          <span><span className="footer-dot" /> Evo interface v0.1</span>
          <span>Interface only · no connected external actions</span>
        </footer>
      </main>
    </div>
  );
}
