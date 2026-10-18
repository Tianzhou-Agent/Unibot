import { api } from "@/lib/api";
import type { AinaCanvasResponse, AinaInstallation, AinaRecord } from "@/types";

// The plugin pages act as the shared demo actor, as the registry page always has.
const ACTOR = { user_id: "anonymous", tenant_id: "default" };
export const ACTOR_QUERY = `user_id=${ACTOR.user_id}&tenant_id=${ACTOR.tenant_id}`;

/** Built-in and bundled first-party AINAs are available to every user without an installation. */
export function isSystemAina(runtime: AinaRecord["manifest"]["runtime"]): boolean {
  return runtime.type === "builtin" || (runtime.type === "package" && Boolean(runtime.bundled));
}

export function installAina(aina: AinaRecord) {
  return api.post(`/ainas/${aina.manifest.aina.id}/install`, {
    ...ACTOR,
    granted_permissions: aina.manifest.permissions,
    configuration: {},
  });
}

/**
 * Sends the user to the sign-in page of an AINA's OAuth tool server; they come back to ``returnTo`` with
 * ``?aina_oauth=connected`` or ``?aina_oauth=failed&message=…``.
 */
export async function connectAina(ainaId: string, returnTo = window.location.pathname) {
  const { authorization_url } = await api.post<{ authorization_url: string }>(
    `/ainas/${encodeURIComponent(ainaId)}/oauth/authorize`,
    { return_to: returnTo },
  );
  window.location.assign(authorization_url);
}

export function disconnectAina(ainaId: string) {
  return api.post<AinaInstallation>(`/ainas/${encodeURIComponent(ainaId)}/oauth/disconnect`);
}

/** Installs an AINA 2.0 package with the scopes and settings the user consented to. */
export function installPackage(aina: AinaRecord, grantedPermissions: string[], configuration: Record<string, unknown>) {
  return api.post<AinaInstallation>(`/ainas/${aina.manifest.aina.id}/install`, {
    ...ACTOR,
    granted_permissions: grantedPermissions,
    configuration,
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
