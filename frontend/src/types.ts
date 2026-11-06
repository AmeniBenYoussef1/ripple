// Shapes returned by the Ripple API (backend/app/main.py and orchestrator.py).

export type NodeType = "resource" | "commitment" | "schedule_item" | "group" | "constraint";

export interface ModelNode {
  id: string;
  type: NodeType;
  label: string;
  attrs: Record<string, unknown>;
  source?: string | null;
}

export interface ModelEdge {
  from: string;
  to: string;
  type: string;
}

export interface EventModel {
  event_id: string;
  title: string;
  nodes: ModelNode[];
  edges: ModelEdge[];
}

export interface Impact {
  origin: string;
  kind: string;
  waves: string[][];
  effects: Record<string, string>;
  checked_ok: string[];
}

export interface Routing {
  run: string[];
  skipped: Record<string, string>;
  cached: Record<string, string>;
  slices: Record<string, string[]>;
}

export interface Disruption {
  node: string;
  kind: string;
  detail: string;
  params: Record<string, unknown>;
}

export interface Candidate {
  name: string;
  url: string;
  area?: string;
  capacity?: number | null;
  est_cost_eur?: number | null;
  fit_notes?: string;
  availability: string;
  address?: string | null;
  lat?: number | null;
  lon?: number | null;
}

export interface Scenario {
  id: string;
  title: string;
  to_confirm: string[];
  extra_cost_eur?: number | null;
  capacity?: number | null;
  changes?: { node?: string; replace_with?: string; set?: Record<string, unknown> }[];
}

export interface Scored {
  id: string;
  title: string;
  feasible: boolean;
  violations: string[];
  worst_group: string | null;
  worst_burden_min: number;
  total_burden_person_min: number;
  extra_cost_eur: number | null;
  unconfirmed: number;
  rank: number | null;
  explanation: string[];
  tied: boolean;
  tied_with: string[];
  spare_seats?: number | null;
}

export interface Message {
  id: string;
  group: string;
  to: string;
  channel: string;
  subject: string;
  body: string;
  status: "draft" | "approved";
}

export interface FileInfo {
  name: string;
  kind: "ics" | "csv";
  rows?: number;
  items?: number;
}

export interface Saved {
  cost: string | null;
  estimate: boolean;
  basis: string;
}

export interface Question {
  prompt: string;
  options: string[];
}

export interface Entry {
  id: string;
  text: string;
  at: string;
  status: "ready" | "questions";
  disruption?: Disruption;
  impact?: Impact;
  routing?: Routing;
  candidates?: Candidate[];
  scenarios?: Scenario[];
  scored?: Scored[];
  coordinator?: "waiting_for_choice" | "running" | "done" | "skipped" | "cached" | "question";
  coordinator_question?: Question[];
  answers?: { answer: string; at: string }[];
  costs?: Record<string, string>;
  saved?: Record<string, Saved>;
  saved_total?: string;
  saved_is_estimate?: boolean;
  outbox?: Message[];
  files?: FileInfo[];
  chosen?: string | null;
  questions?: Question[];
}

export interface EventState {
  event_id: string;
  search_area: string;
  model: EventModel;
  history: Entry[];
  costs: { disruption: string; agent: string; cost: string }[];
  demo_mode: boolean;
  total_cost: string;
  saved_total: string;
  files: string[];
  setup?: SetupInfo;
  /** Replay only: what is recorded per problem (anything else is Live mode only). */
  replayable?: Record<string, { options: string[]; answers: string[] }> | null;
}

export interface SetupInfo {
  status: "not_analyzed" | "analyzed";
  replay?: boolean;
  at?: string;
  files?: { name: string; bytes: number }[];
  cost?: string;
  confirmed?: boolean;
}

export type AgentName = "impact_analyst" | "resource_finder" | "replanner" | "coordinator";
export type AgentStatus = "idle" | "waiting" | "working" | "done" | "skipped" | "cached";

export interface AgentView {
  status: AgentStatus;
  detail: string;
}

/** What the screen shows for one disruption: a saved entry, or one still running (live). */
export interface View extends Partial<Entry> {
  id: string;
  text: string;
  agents: Record<AgentName, AgentView>;
}

export interface VenuePin {
  name: string;
  url: string;
  address?: string | null;
  area?: string | null;
  capacity?: number | null;
  lat: number | null;
  lon: number | null;
  located?: string | null;
}

export interface Venues {
  disruption: string | null;
  lost: { name: string; address?: string | null; lat: number | null; lon: number | null; note: string | null } | null;
  candidates: VenuePin[];
}
