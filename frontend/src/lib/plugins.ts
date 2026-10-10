import { api } from "@/lib/api";
import type { AinaCanvasResponse, AinaRecord } from "@/types";

// The plugin pages act as the shared demo actor, as the registry page always has.
const ACTOR = { user_id: "anonymous", tenant_id: "default" };
export const ACTOR_QUERY = `user_id=${ACTOR.user_id}&tenant_id=${ACTOR.tenant_id}`;

export function installAina(aina: AinaRecord) {
  return api.post(`/ainas/${aina.manifest.aina.id}/install`, {
    ...ACTOR,
    granted_permissions: aina.manifest.permissions,
    configuration: {},
  });
}

export function uninstallAina(ainaId: string) {
  return api.delete(`/ainas/${ainaId}/install?${ACTOR_QUERY}`);
}

export function deleteAina(ainaId: string) {
  return api.delete(`/ainas/${ainaId}`);
}

/** Opens the AINA's canvas and returns the route to navigate to. */
export async function openAina(ainaId: string): Promise<string> {
  const canvas = await api.post<AinaCanvasResponse>(`/ainas/${ainaId}/open`, ACTOR);
  return canvas.route;
}
