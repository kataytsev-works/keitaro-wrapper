import type { OfferStats } from "./api";

/** Report rows keyed by flow and offer; offer 0 holds clicks of a flow without offers. */
export type StatsByOffer = Map<string, OfferStats>;

export function statsKey(streamId: number, offerId: number): string {
  return `${streamId}:${offerId}`;
}
