/**
 * Shapes returned by /api/extensions/*.
 *
 * The status vocabulary is deliberately split in two and the UI must not
 * collapse it. `packageState` describes bytes in managed storage -- imported
 * or downloaded. Per-profile `state` is the only thing allowed to say
 * installed, and only after the browser was observed running the extension.
 * See src/multiprofile/extensions/manager.py.
 */

export type AssignmentState =
  | "assigned"
  | "pending_restart"
  | "installed_verified"
  | "failed";

export interface Assignment {
  extensionId: string;
  profileId: string;
  enabled: boolean;
  state: AssignmentState;
  stateLabel: string;
  assignedAt: string;
  appliedAt: string | null;
  verifiedAt: string | null;
  observedExtensionId: string | null;
  observedVersion: string | null;
  lastError: string | null;
}

export interface ManagedExtension {
  id: string;
  name: string;
  version: string;
  manifestVersion: number;
  description: string;
  source: string;
  sourceDetail: string;
  permissions: string[];
  hostPermissions: string[];
  hasKey: boolean;
  payloadPath: string;
  sizeBytes: number;
  /** "imported" | "downloaded" — never "installed". */
  packageState: string;
  /** The ID we expect the browser to report, derived from the managed path. */
  extensionId: string | null;
  defaultForNewProfiles: boolean;
  createdAt: string;
  updatedAt: string;
  assignedProfileCount: number;
  verifiedProfileCount: number;
  assignmentStates: Partial<Record<AssignmentState, number>>;
  payloadPresent: boolean;
  assignments: Assignment[];
}

export interface Capability {
  binary: string | null;
  brand: string | null;
  version: string | null;
  major: number | null;
  unpackedLoading: boolean;
  crxSideload: boolean;
  maxManifestVersion: number;
  supported: boolean;
  reason: string;
  remediation: string;
}

export interface ExtensionsResponse {
  ok: boolean;
  extensions: ManagedExtension[];
  capability: Capability;
  stateLabels: Record<string, string>;
}

export interface SelectableProfile {
  id: string;
  name: string | null;
  tags: string[];
  isRunning: boolean;
}

export interface ProfileResultRow {
  profileId: string;
  profileName: string;
  ok: boolean;
  state: string;
  stateLabel: string;
  detail: string;
}

export interface BulkResponse {
  ok: boolean;
  total: number;
  succeeded: number;
  failed: number;
  failedProfileIds: string[];
  results: ProfileResultRow[];
  skipped?: string[];
  missingProfileIds?: string[];
}

export interface ManifestPreview {
  name: string;
  version: string;
  manifestVersion: number;
  description: string;
  permissions: string[];
  hostPermissions: string[];
  hasKey: boolean;
}

export interface PreviewResponse {
  ok: boolean;
  manifest: ManifestPreview;
  root: string;
  members?: number;
  totalBytes?: number;
  missingReferencedFiles: string[];
  capability: Capability;
}

/** Per-profile state → badge tone. */
export function stateTone(state: string): "ok" | "warn" | "err" | "info" | "idle" {
  switch (state) {
    case "installed_verified":
      return "ok";
    case "pending_restart":
      return "warn";
    case "failed":
      return "err";
    case "assigned":
      return "info";
    default:
      return "idle";
  }
}

export interface EngineChoice {
  binary: string | null;
  origin: "env" | "persisted" | "path" | "missing";
  pinned: boolean;
  brand: string | null;
  version: string | null;
  major: number | null;
  unpackedLoading: boolean;
  reason: string;
  remediation: string;
  driftedFrom: string | null;
  driftBreaksExtensions: boolean;
  notes: string[];
  choiceFile: string;
}

export interface UpdatePreview {
  ok: boolean;
  extensionId: string;
  name: string;
  currentVersion: string;
  /** Always "all-assignments". There is no per-profile version. */
  scope: string;
  affectedProfileCount: number;
  affectedProfiles: {
    profileId: string;
    profileName: string;
    isRunning: boolean;
    state: AssignmentState;
    stateLabel: string;
    enabled: boolean;
  }[];
  runningProfileCount: number;
  rollbackAvailable: boolean;
  retainedVersions: { version: string; payloadPath: string; createdAt: string }[];
  /** The sentence the operator must see before an update runs. */
  scopeStatement: string;
}
