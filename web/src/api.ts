// Typed client for the FastAPI backend. Types mirror salesintel/api/queries.py responses.

export type Tier = "A" | "B" | "C";

export interface FacetValue { value: string; count: number }
export interface CountryFacet extends FacetValue { label: string; region: string }
export interface SignalFacet { key: string; label: string; category: string; weight: number; count: number }

export interface Meta {
  dataset: { records: number; ips: number; orgs: number; honeypots: number; with_cves: number; with_kev: number; last_seen: string } | null;
  build: { accounts_total: number; accounts_with_signals: number; built_at: string } | null;
  resolution: Record<string, number> | null;
  facets: {
    regions: FacetValue[]; industries: FacetValue[]; segments: FacetValue[]; hosting: FacetValue[];
    tiers: FacetValue[]; countries: CountryFacet[]; signals: SignalFacet[];
  };
  llm: { mode: "live" | "offline"; prompts: Record<string, string>; daily_budget_usd: number; spent_today_usd: number };
}

export interface AccountListItem {
  id: number; name: string; domain: string | null; country_code: string | null; country: string | null;
  region: string; industry: string; industry_source: string; segment: string; hosting: string;
  entity_type: string | null; is_prospect: number; prospect_source: string; n_ips: number;
  need: number; fit: number; priority: number; tier: Tier; top_reason: string | null;
  signals: Record<string, number>; max_cvss: number | null; max_epss: number | null; n_cves: number;
  shortlisted: boolean;
}

export interface AccountList {
  total: number; page: number; page_size: number; tier_counts: Partial<Record<Tier, number>>;
  items: AccountListItem[];
}

export interface Reason { signal: string; label: string; category: string; count: number; points: number; text: string }

export interface Evidence {
  ref: string; ip: string; port: number; transport: string; module: string | null; product: string | null;
  version: string | null; http_title: string | null; http_host: string | null; ssl_cn: string | null;
  org: string | null; seen_at: string; cves: string[]; n_cves: number; max_cvss: number | null;
  max_epss: number | null; kev_count: number; flags: string[]; flag_labels: string[]; severity: number;
}

export interface Classification {
  entity_type: string; is_prospect: boolean; industry: string; confidence: string; rationale: string;
}

export interface OutreachDraft {
  summary: string;
  why_now: { point: string; evidence: string[] }[];
  talk_track: string[];
  email: { subject: string; body: string };
  cited_cves: string[];
}

export interface LLMOutput<T> {
  output: T; prompt_version: string; model: string; provider: string; created_at: string; call_id: string;
  checks?: { passed: boolean; issues: string[]; metrics: Record<string, number> } | null;
  cached?: boolean; status?: string; note?: string | null;
}

export interface AccountDetail {
  account: AccountListItem & {
    account_key: string; key_source: string; n_services: number; n_ports: number; primary_org: string;
    titles: string[]; products: string[]; hosts: string[]; hosting_orgs: string[]; cert_orgs: string[];
    fit_breakdown: Record<string, number>; industry_clue: string | null; waf_share: number | null;
    has_security_txt: number; last_seen: string;
  };
  reasons: Reason[];
  evidence: Evidence[];
  evidence_total: number;
  shortlisted: boolean;
  classification: LLMOutput<Classification> | null;
  outreach: LLMOutput<OutreachDraft> | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  meta: () => request<Meta>("/api/meta"),
  accounts: (params: URLSearchParams) => request<AccountList>(`/api/accounts?${params}`),
  account: (id: number) => request<AccountDetail>(`/api/accounts/${id}`),
  classify: (id: number, refresh = false) =>
    request<LLMOutput<Classification>>(`/api/accounts/${id}/classify?refresh=${refresh}`, { method: "POST" }),
  outreach: (id: number, refresh = false) =>
    request<LLMOutput<OutreachDraft>>(`/api/accounts/${id}/outreach?refresh=${refresh}`, { method: "POST" }),
  shortlist: (id: number, on: boolean) =>
    request<{ shortlisted: boolean }>(`/api/shortlist/${id}`, { method: on ? "PUT" : "DELETE" }),
  ops: () => request<Ops>("/api/ops"),
  exportUrl: (params: URLSearchParams) => `/api/export.csv?${params}`,
};

export interface Ops {
  llm_mode: "live" | "offline";
  budget: { daily_usd: number; spent_today_usd: number };
  usage_30d: { env: string; feature: string; prompt_version: string; provider: string; model: string; status: string;
    calls: number; cost_usd: number; input_tokens: number; output_tokens: number; avg_latency_ms: number }[];
  latency_ms: { p50: number | null; p95: number | null; mean: number | null };
  recent_calls: { call_id: string; ts: string; env: string; feature: string; prompt_version: string; provider: string;
    model: string; account_id: number | null; status: string; decision: string | null; input_tokens: number;
    output_tokens: number; latency_ms: number | null; cost_usd: number; error: string | null }[];
  cost_model: {
    rows: { feature: string; model: string; why_this_model: string; volume_note: string; calls_per_month: number;
      cost_per_call_usd: number; monthly_usd: number; tokens_source: string; batch_discount: boolean }[];
    ceilings: Record<string, number>;
  };
  evals: Record<string, { version: string; status: string; reason?: string; headline?: Record<string, number | null>;
    ops?: { model: string; cost_per_example_usd: number; latency_p50_ms: number }; previous?: Record<string, number> | null }[]>;
  prompts: Record<string, { active: string; versions: string[] }>;
}
