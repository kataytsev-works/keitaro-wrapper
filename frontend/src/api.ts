// Typed client for the wrapper's backend (/api). Types mirror backend/app/api/schemas.py.

export type OfferStatus = "active" | "removed" | "missing";
export type StatsPeriod = "today" | "yesterday" | "7d";

export interface Offer {
  id: number;
  name: string | null;
}

export interface KeitaroCampaign {
  id: number;
  name: string;
  alias: string;
}

export interface StreamOffer {
  offer_id: number;
  name: string | null;
  share: number;
  is_pinned: boolean;
  status: OfferStatus;
  position: number;
}

export interface Stream {
  id: number;
  name: string;
  position: number;
  schema: string;
  is_editable: boolean;
  has_unpushed_changes: boolean;
  offers: StreamOffer[];
}

export interface CampaignSummary {
  id: number;
  name: string;
  synced_at: string;
  has_unpushed_changes: boolean;
}

export interface Campaign extends CampaignSummary {
  streams: Stream[];
}

export interface NamedItem {
  id: number;
  name: string;
}

export interface CreatorOptions {
  domains: NamedItem[];
  groups: NamedItem[];
  traffic_sources: NamedItem[];
  offers: Offer[];
}

export interface NewCampaign {
  name: string;
  countries: string[];
  offer_id: number;
  domain_id: number;
  group_id: number;
  traffic_source_id: number;
}

export interface OfferStats {
  stream_id: number;
  offer_id: number;
  clicks: number;
  unique_clicks: number;
  conversions: number;
  revenue: number;
}

/** Error answer of the backend: `detail` is a human-readable reason. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload: unknown = await response.json();
      if (payload && typeof payload === "object" && "detail" in payload) {
        detail =
          typeof payload.detail === "string"
            ? payload.detail
            : JSON.stringify(payload.detail);
      }
    } catch {
      // not JSON: keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export const api = {
  keitaroCampaigns: () =>
    request<KeitaroCampaign[]>("GET", "/keitaro/campaigns"),
  offers: () => request<Offer[]>("GET", "/offers"),
  campaigns: () => request<CampaignSummary[]>("GET", "/campaigns"),
  creatorOptions: () => request<CreatorOptions>("GET", "/creator/options"),
  createCampaign: (data: NewCampaign) =>
    request<Campaign>("POST", "/campaigns", data),
  campaign: (id: number) => request<Campaign>("GET", `/campaigns/${id}`),
  importCampaign: (campaignId: number) =>
    request<Campaign>("POST", "/campaigns/import", { campaign_id: campaignId }),
  refresh: (id: number) => request<Campaign>("POST", `/campaigns/${id}/sync`),
  discard: (id: number) =>
    request<Campaign>("POST", `/campaigns/${id}/discard`),
  push: (id: number) => request<Campaign>("POST", `/campaigns/${id}/push`),
  stats: (id: number, period: StatsPeriod) =>
    request<OfferStats[]>("GET", `/campaigns/${id}/stats?period=${period}`),
  addOffer: (streamId: number, offerId: number) =>
    request<Campaign>("POST", `/streams/${streamId}/offers`, {
      offer_id: offerId,
    }),
  removeOffer: (streamId: number, offerId: number) =>
    request<Campaign>("POST", `/streams/${streamId}/offers/${offerId}/remove`),
  restoreOffer: (streamId: number, offerId: number) =>
    request<Campaign>("POST", `/streams/${streamId}/offers/${offerId}/restore`),
  deleteOffer: (streamId: number, offerId: number) =>
    request<Campaign>("DELETE", `/streams/${streamId}/offers/${offerId}`),
  setPin: (streamId: number, offerId: number, share: number | null) =>
    request<Campaign>("PUT", `/streams/${streamId}/offers/${offerId}/pin`, {
      share,
    }),
};

/** Offer label: its name, or "Offer #id" when Keitaro does not let us read it. */
export function offerLabel(offer: {
  offer_id: number;
  name: string | null;
}): string {
  return offer.name ?? `Оффер #${offer.offer_id}`;
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}
