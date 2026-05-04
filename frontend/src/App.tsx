import {
  AlertTriangle,
  Binary,
  Bot,
  Braces,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  FileCode2,
  FolderTree,
  GitBranch,
  Loader2,
  MessageSquarePlus,
  PanelRightOpen,
  Send,
  Sparkles,
} from "lucide-react";
import { FormEvent, ReactNode, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Citation,
  ChatMessage,
  ChatSession,
  FileEntry,
  createChatSession,
  getFileContent,
  getIngestionJob,
  listChatMessages,
  listChatSessions,
  listFileTree,
  listRepositories,
  refreshRepository,
  streamChatMessage,
  submitRepository,
} from "./api";

type StreamState = {
  text: string;
  citations: Citation[];
  status: "idle" | "retrieving" | "streaming" | "error";
  error: string | null;
};

const EMPTY_STREAM: StreamState = { text: "", citations: [], status: "idle", error: null };

export default function App() {
  const queryClient = useQueryClient();
  const [repoUrl, setRepoUrl] = useState("");
  const [selectedRepoId, setSelectedRepoId] = useState<string | null>(null);
  const [trackedJobId, setTrackedJobId] = useState<string | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [stream, setStream] = useState<StreamState>(EMPTY_STREAM);

  const repositories = useQuery({
    queryKey: ["repositories"],
    queryFn: listRepositories,
    refetchInterval: 3500,
  });

  const activeRepoId = selectedRepoId ?? repositories.data?.repositories[0]?.repository_id ?? null;

  const selectedRepo = useMemo(
    () => repositories.data?.repositories.find((repo) => repo.repository_id === activeRepoId) ?? null,
    [activeRepoId, repositories.data?.repositories],
  );

  const job = useQuery({
    queryKey: ["ingestion-job", trackedJobId],
    queryFn: () => getIngestionJob(trackedJobId!),
    enabled: trackedJobId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "succeeded" || status === "failed" ? false : 1800;
    },
  });

  const sessions = useQuery({
    queryKey: ["chat-sessions", activeRepoId],
    queryFn: () => listChatSessions(activeRepoId!),
    enabled: activeRepoId !== null,
  });

  const activeSessionId = selectedSessionId ?? sessions.data?.sessions[0]?.session_id ?? null;

  const messages = useQuery({
    queryKey: ["chat-messages", activeSessionId],
    queryFn: () => listChatMessages(activeSessionId!),
    enabled: activeSessionId !== null,
  });

  const fileTree = useQuery({
    queryKey: ["file-tree", activeRepoId],
    queryFn: () => listFileTree(activeRepoId!),
    enabled: activeRepoId !== null,
  });

  const filePreview = useQuery({
    queryKey: ["file-content", activeRepoId, selectedFilePath],
    queryFn: () => getFileContent(activeRepoId!, selectedFilePath!),
    enabled: activeRepoId !== null && selectedFilePath !== null,
  });

  const submitRepo = useMutation({
    mutationFn: submitRepository,
    onSuccess: (data) => {
      setRepoUrl("");
      setSelectedRepoId(data.repository_id);
      setTrackedJobId(data.job_id);
      void queryClient.invalidateQueries({ queryKey: ["repositories"] });
    },
  });

  const refreshRepo = useMutation({
    mutationFn: () => refreshRepository(activeRepoId!),
    onSuccess: (data) => {
      setTrackedJobId(data.job_id);
      void queryClient.invalidateQueries({ queryKey: ["repositories"] });
      void queryClient.invalidateQueries({ queryKey: ["file-tree", activeRepoId] });
    },
  });

  const createSession = useMutation({
    mutationFn: () => createChatSession(activeRepoId!, `Investigation ${new Date().toLocaleTimeString()}`),
    onSuccess: (session) => {
      setSelectedSessionId(session.session_id);
      void queryClient.invalidateQueries({ queryKey: ["chat-sessions", activeRepoId] });
    },
  });

  const activeCitations = stream.citations.length > 0 ? stream.citations : collectCitations(messages.data?.messages);
  const visibleEntries = fileTree.data?.entries.filter((entry) => entry.kind === "file").slice(0, 180) ?? [];

  function handleSubmitRepo(event: FormEvent) {
    event.preventDefault();
    if (repoUrl.trim()) submitRepo.mutate(repoUrl.trim());
  }

  async function handleSend(event: FormEvent) {
    event.preventDefault();
    if (!activeSessionId || !prompt.trim() || stream.status === "streaming") return;

    const outgoing = prompt.trim();
    setPrompt("");
    setStream({ ...EMPTY_STREAM, status: "retrieving" });

    try {
      await streamChatMessage(activeSessionId, outgoing, selectedRepo?.active_snapshot_id ?? null, (event) => {
        if (event.event === "retrieval_started") {
          setStream((current) => ({ ...current, status: "retrieving" }));
        }
        if (event.event === "sources") {
          setStream((current) => ({
            ...current,
            status: "streaming",
            citations: event.data.citations ?? event.data.sources ?? [],
          }));
        }
        if (event.event === "token") {
          setStream((current) => ({
            ...current,
            status: "streaming",
            text: current.text + (event.data.token ?? event.data.delta ?? ""),
          }));
        }
        if (event.event === "final") {
          setStream({
            text: event.data.message?.content ?? event.data.content ?? "",
            citations: event.data.message?.citations ?? event.data.citations ?? [],
            status: "idle",
            error: null,
          });
          void queryClient.invalidateQueries({ queryKey: ["chat-messages", activeSessionId] });
        }
        if (event.event === "error") {
          setStream((current) => ({ ...current, status: "error", error: event.data.message }));
        }
      });
    } catch (error) {
      setStream({ ...EMPTY_STREAM, status: "error", error: error instanceof Error ? error.message : "Stream failed." });
    }
  }

  function openCitation(citation: Citation) {
    setSelectedCitation(citation);
    setSelectedFilePath(citation.path);
  }

  return (
    <main className="app-shell">
      <section className="hero-card reveal-a">
        <div>
          <p className="eyebrow"><Sparkles size={14} /> Local public-repo RAG console</p>
          <h1>Interrogate a codebase without losing sight of the source.</h1>
        </div>
        <form className="repo-form" onSubmit={handleSubmitRepo}>
          <input
            value={repoUrl}
            onChange={(event) => setRepoUrl(event.target.value)}
            placeholder="https://github.com/owner/repo"
            aria-label="Public GitHub repository URL"
          />
          <button disabled={submitRepo.isPending}>{submitRepo.isPending ? "Queueing" : "Index Repo"}</button>
        </form>
      </section>

      <section className="workspace-grid">
        <aside className="panel repo-panel reveal-b">
          <PanelTitle icon={<GitBranch size={17} />} title="Repositories" />
          <div className="repo-stack">
            {repositories.data?.repositories.map((repo) => (
              <button
                className={`repo-tile ${repo.repository_id === selectedRepoId ? "active" : ""}`}
                key={repo.repository_id}
                onClick={() => {
                  setSelectedRepoId(repo.repository_id);
                  setSelectedSessionId(null);
                  setSelectedFilePath(null);
                }}
              >
                <span>{repo.owner}/{repo.name}</span>
                <small>{repo.phase} / {repo.status}</small>
              </button>
            ))}
            {repositories.data?.repositories.length === 0 && <EmptyState text="Submit a public GitHub HTTPS URL to begin." />}
          </div>

          <button
            className="refresh-button"
            disabled={!activeRepoId || refreshRepo.isPending}
            onClick={() => refreshRepo.mutate()}
          >
            <GitBranch size={15} /> {refreshRepo.isPending ? "Refreshing" : "Refresh index"}
          </button>
          <StatusCard repo={selectedRepo} job={job.data ?? null} />

          <div className="sessions-head">
            <PanelTitle icon={<MessageSquarePlus size={17} />} title="Sessions" />
            <button className="ghost-button" disabled={!activeRepoId} onClick={() => createSession.mutate()}>New</button>
          </div>
          <SessionList sessions={sessions.data?.sessions ?? []} selected={activeSessionId} onSelect={setSelectedSessionId} />
        </aside>

        <section className="panel chat-panel reveal-c">
          <PanelTitle icon={<Bot size={18} />} title="Chat" />
          <div className="message-list">
            {messages.data?.messages.map((message) => (
              <MessageBubble key={message.message_id} message={message} onCitation={openCitation} />
            ))}
            {stream.text && <StreamingBubble stream={stream} onCitation={openCitation} />}
            {stream.status === "retrieving" && <div className="thinking"><Loader2 className="spin" size={16} /> collecting repo-scoped evidence</div>}
            {stream.error && <div className="stream-error"><AlertTriangle size={16} /> {stream.error}</div>}
            {!activeSessionId && <EmptyState text="Create or select a session to ask codebase questions." />}
          </div>
          <form className="prompt-form" onSubmit={handleSend}>
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Ask where auth is wired, how indexing works, or why a file is skipped..."
              aria-label="Chat prompt"
            />
            <button disabled={!activeSessionId || !prompt.trim() || stream.status === "streaming"}>
              <Send size={17} /> Send
            </button>
          </form>
        </section>

        <aside className="panel source-panel reveal-d">
          <PanelTitle icon={<PanelRightOpen size={17} />} title="Evidence" />
          <CitationPanel citations={activeCitations} selected={selectedCitation} onOpen={openCitation} />

          <PanelTitle icon={<FolderTree size={17} />} title="Files" />
          <div className="file-list">
            {visibleEntries.map((entry) => (
              <FileRow key={entry.path} entry={entry} active={entry.path === selectedFilePath} onOpen={setSelectedFilePath} />
            ))}
          </div>

          <FilePreviewPanel preview={filePreview.data} citation={selectedCitation} loading={filePreview.isFetching} />
        </aside>
      </section>
    </main>
  );
}

function PanelTitle({ icon, title }: { icon: ReactNode; title: string }) {
  return <h2 className="panel-title">{icon}{title}</h2>;
}

function StatusCard({ repo, job }: { repo: { status: string; phase: string; warnings: string[]; skipped: Record<string, number>; active_snapshot_id: string | null } | null; job: { status: string; phase: string; error: string | null; warnings: string[]; skipped: Record<string, number> } | null }) {
  const target = job ?? repo;
  if (!target) return null;
  const skipped = Object.entries(target.skipped ?? {});
  return (
    <div className="status-card">
      <div><CircleDot size={14} /> {target.phase}</div>
      <strong>{target.status}</strong>
      {"active_snapshot_id" in target && target.active_snapshot_id && <small>Snapshot {target.active_snapshot_id}</small>}
      {target.warnings?.map((warning) => <p key={warning} className="warning"><AlertTriangle size={14} /> {warning}</p>)}
      {"error" in target && target.error && <p className="warning"><AlertTriangle size={14} /> {target.error}</p>}
      {skipped.length > 0 && <small>{skipped.map(([reason, count]) => `${count} ${reason}`).join(" / ")}</small>}
    </div>
  );
}

function SessionList({ sessions, selected, onSelect }: { sessions: ChatSession[]; selected: string | null; onSelect: (id: string) => void }) {
  return <div className="session-list">{sessions.map((session) => <button className={session.session_id === selected ? "active" : ""} key={session.session_id} onClick={() => onSelect(session.session_id)}><ChevronRight size={14} />{session.title}</button>)}</div>;
}

function MessageBubble({ message, onCitation }: { message: ChatMessage; onCitation: (citation: Citation) => void }) {
  return <article className={`message ${message.role}`}><p>{message.content}</p><InlineCitations citations={message.citations} onCitation={onCitation} /></article>;
}

function StreamingBubble({ stream, onCitation }: { stream: StreamState; onCitation: (citation: Citation) => void }) {
  return <article className="message assistant streaming"><p>{stream.text}</p><InlineCitations citations={stream.citations} onCitation={onCitation} /></article>;
}

function InlineCitations({ citations, onCitation }: { citations: Citation[]; onCitation: (citation: Citation) => void }) {
  if (citations.length === 0) return null;
  return <div className="inline-citations">{citations.map((citation) => <button key={`${citation.path}:${citation.start_line}`} onClick={() => onCitation(citation)}>{citation.path}:{citation.start_line}-{citation.end_line}</button>)}</div>;
}

function CitationPanel({ citations, selected, onOpen }: { citations: Citation[]; selected: Citation | null; onOpen: (citation: Citation) => void }) {
  return <div className="citation-list">{citations.slice(0, 8).map((citation) => <button className={selected?.path === citation.path && selected.start_line === citation.start_line ? "active" : ""} key={`${citation.path}:${citation.start_line}`} onClick={() => onOpen(citation)}><Braces size={15} /><span>{citation.path}</span><small>Lines {citation.start_line}-{citation.end_line}</small></button>)}{citations.length === 0 && <EmptyState text="Citations from streamed answers appear here." />}</div>;
}

function FileRow({ entry, active, onOpen }: { entry: FileEntry; active: boolean; onOpen: (path: string) => void }) {
  return <button className={`file-row ${active ? "active" : ""}`} onClick={() => onOpen(entry.path)}><FileCode2 size={15} /><span>{entry.path}</span>{!entry.indexable && <em><Binary size={12} /> {entry.skipped_reason ?? "not indexed"}</em>}</button>;
}

function FilePreviewPanel({ preview, citation, loading }: { preview: { path: string; content: string; previewable: boolean; reason: string | null } | undefined; citation: Citation | null; loading: boolean }) {
  if (loading) return <div className="code-preview loading"><Loader2 className="spin" size={16} /> Loading source</div>;
  if (!preview) return <div className="code-preview muted">Select a file or citation to inspect read-only source.</div>;
  if (!preview.previewable) return <div className="code-preview blocked"><AlertTriangle size={16} /> Preview blocked: {preview.reason ?? "unsafe preview"}</div>;
  return <pre className="code-preview"><code>{highlightRange(preview.content, citation?.path === preview.path ? citation : null)}</code></pre>;
}

function highlightRange(content: string, citation: Citation | null) {
  return content.split("\n").map((line, index) => {
    const lineNo = index + 1;
    const mark = citation && lineNo >= citation.start_line && lineNo <= citation.end_line ? "›" : " ";
    return `${mark} ${String(lineNo).padStart(4, " ")}  ${line}`;
  }).join("\n");
}

function collectCitations(messages: ChatMessage[] | undefined) {
  return messages?.flatMap((message) => message.citations) ?? [];
}

function EmptyState({ text }: { text: string }) {
  return <div className="empty-state"><CheckCircle2 size={15} />{text}</div>;
}
