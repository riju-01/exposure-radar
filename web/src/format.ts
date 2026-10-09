const LABELS: Record<string, string> = {
  healthcare: "Healthcare", finance: "Finance", manufacturing: "Manufacturing", education: "Education",
  retail_ecommerce: "Retail & e-commerce", professional_services: "Professional services",
  technology: "Technology", media_entertainment: "Media & entertainment", government: "Government",
  nonprofit: "Nonprofit", hosting_telecom: "Hosting / telecom", unknown: "Unknown",
  micro: "Micro (1 IP)", small: "Small (2–5)", mid_market: "Mid-market (6–50)", enterprise: "Enterprise (50+)",
  self_hosted: "Self-hosted", hybrid: "Hybrid", cloud: "Cloud",
  business: "Business", healthcare_provider: "Healthcare provider", hosting_provider: "Hosting provider",
  telecom_isp: "Telecom / ISP", cdn_saas_platform: "CDN / SaaS platform", individual_or_hobby: "Individual / hobby",
  http_host: "website host", ssl_cn: "TLS certificate", rdns: "reverse DNS", org: "network owner",
};

export const label = (v: string | null | undefined) => (v ? LABELS[v] ?? v : "—");

export const num = (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString());

export const usd = (n: number, digits = 4) => `$${n.toFixed(digits)}`;

export function timeAgo(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

/** Scan data stores page titles raw; decode HTML entities like &nbsp; / &#8211; for display. */
export function decodeEntities(s: string): string {
  const el = document.createElement("textarea");
  el.innerHTML = s;
  return el.value;
}
