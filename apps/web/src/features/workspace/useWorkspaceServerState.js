import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { api } from "../../api.js";

export const workspaceKeys = {
  atlases: ["workspace", "atlases"],
  projects: ["workspace", "projects"],
  threads: ["workspace", "threads"],
  secrets: ["workspace", "secrets"],
  researchTemplates: ["workspace", "research-templates"],
  systemInfo: ["workspace", "system-info"],
  knowledgeStatus: ["workspace", "knowledge-status"],
  atlasBundle: (atlasId) => ["workspace", "atlas", atlasId, "bundle"],
  objectMemories: (atlasId) => ["workspace", "atlas", atlasId, "object-memories"],
  atlasUpdates: (atlasId) => ["workspace", "atlas", atlasId, "updates"]
};

const queryFns = {
  atlases: () => api("/atlases"),
  projects: () => api("/projects"),
  threads: () => api("/threads"),
  secrets: () => api("/secrets/status"),
  researchTemplates: () => api("/research-templates"),
  systemInfo: () => api("/system/info").catch(() => null),
  knowledgeStatus: () => api("/knowledge/status").catch(() => null),
  atlasBundle: (atlasId) => api(`/atlases/${encodeURIComponent(atlasId)}/bundle`),
  objectMemories: (atlasId) => api(`/object-memory?atlas_id=${encodeURIComponent(atlasId)}`),
  atlasUpdates: (atlasId) => api(`/atlas-updates/${encodeURIComponent(atlasId)}`)
};

function useAtlasQuery(queryKey, queryFn, enabled) {
  return useQuery({
    queryKey,
    queryFn,
    enabled,
    placeholderData: keepPreviousData
  });
}

export function useAtlasServerState(atlasId) {
  const enabled = Boolean(atlasId);
  const bundleQuery = useAtlasQuery(
    workspaceKeys.atlasBundle(atlasId),
    () => queryFns.atlasBundle(atlasId),
    enabled
  );
  const objectMemoriesQuery = useAtlasQuery(
    workspaceKeys.objectMemories(atlasId),
    () => queryFns.objectMemories(atlasId),
    enabled
  );
  const atlasUpdatesQuery = useAtlasQuery(
    workspaceKeys.atlasUpdates(atlasId),
    () => queryFns.atlasUpdates(atlasId),
    enabled
  );

  return { bundleQuery, objectMemoriesQuery, atlasUpdatesQuery };
}

export function useWorkspaceServerState(activeAtlasId) {
  const queryClient = useQueryClient();
  const atlasesQuery = useQuery({ queryKey: workspaceKeys.atlases, queryFn: queryFns.atlases });
  const projectsQuery = useQuery({ queryKey: workspaceKeys.projects, queryFn: queryFns.projects });
  const threadsQuery = useQuery({ queryKey: workspaceKeys.threads, queryFn: queryFns.threads });
  const secretsQuery = useQuery({ queryKey: workspaceKeys.secrets, queryFn: queryFns.secrets });
  const researchTemplatesQuery = useQuery({
    queryKey: workspaceKeys.researchTemplates,
    queryFn: queryFns.researchTemplates
  });
  const systemInfoQuery = useQuery({ queryKey: workspaceKeys.systemInfo, queryFn: queryFns.systemInfo });
  const knowledgeStatusQuery = useQuery({
    queryKey: workspaceKeys.knowledgeStatus,
    queryFn: queryFns.knowledgeStatus,
    refetchInterval: (query) => query.state.data?.active_jobs?.length ? 1200 : false
  });
  const { bundleQuery, objectMemoriesQuery, atlasUpdatesQuery } = useAtlasServerState(activeAtlasId);

  const requiredQueries = [
    atlasesQuery,
    projectsQuery,
    threadsQuery,
    secretsQuery,
    researchTemplatesQuery,
    systemInfoQuery,
    knowledgeStatusQuery
  ];

  const refreshQuery = useCallback(async (queryKey, queryFn) => {
    await queryClient.invalidateQueries({ queryKey, exact: true });
    return queryClient.fetchQuery({ queryKey, queryFn });
  }, [queryClient]);

  const refreshThreads = useCallback(
    () => refreshQuery(workspaceKeys.threads, queryFns.threads),
    [refreshQuery]
  );
  const refreshProjects = useCallback(
    () => refreshQuery(workspaceKeys.projects, queryFns.projects),
    [refreshQuery]
  );
  const refreshKnowledgeStatus = useCallback(
    () => refreshQuery(workspaceKeys.knowledgeStatus, queryFns.knowledgeStatus),
    [refreshQuery]
  );
  const refreshRuntimeState = useCallback(
    () => Promise.all([
      refreshQuery(workspaceKeys.secrets, queryFns.secrets),
      refreshQuery(workspaceKeys.systemInfo, queryFns.systemInfo)
    ]),
    [refreshQuery]
  );
  const refreshBundle = useCallback(
    (atlasId) => refreshQuery(workspaceKeys.atlasBundle(atlasId), () => queryFns.atlasBundle(atlasId)),
    [refreshQuery]
  );
  const refreshObjectMemories = useCallback(
    (atlasId) => refreshQuery(workspaceKeys.objectMemories(atlasId), () => queryFns.objectMemories(atlasId)),
    [refreshQuery]
  );
  const refreshAtlasUpdates = useCallback(
    (atlasId) => refreshQuery(workspaceKeys.atlasUpdates(atlasId), () => queryFns.atlasUpdates(atlasId)),
    [refreshQuery]
  );

  const setProjects = useCallback(
    (value) => queryClient.setQueryData(workspaceKeys.projects, value),
    [queryClient]
  );
  const setThreads = useCallback(
    (value) => queryClient.setQueryData(workspaceKeys.threads, value),
    [queryClient]
  );
  const setObjectMemories = useCallback((value, atlasId = activeAtlasId) => {
    if (atlasId) queryClient.setQueryData(workspaceKeys.objectMemories(atlasId), value);
  }, [activeAtlasId, queryClient]);
  const setAtlasUpdates = useCallback((value, atlasId = activeAtlasId) => {
    if (atlasId) queryClient.setQueryData(workspaceKeys.atlasUpdates(atlasId), value);
  }, [activeAtlasId, queryClient]);

  return {
    atlases: atlasesQuery.data ?? [],
    projects: projectsQuery.data ?? [],
    threads: threadsQuery.data ?? [],
    secrets: secretsQuery.data ?? { configured: false, providers: [] },
    researchTemplates: researchTemplatesQuery.data ?? [],
    systemInfo: systemInfoQuery.data ?? null,
    knowledgeStatus: knowledgeStatusQuery.data ?? null,
    bundle: bundleQuery.data ?? null,
    objectMemories: objectMemoriesQuery.data ?? [],
    atlasUpdates: atlasUpdatesQuery.data ?? null,
    bootstrapReady: requiredQueries.every((query) => query.isSuccess),
    bootstrapError: requiredQueries.find((query) => query.error)?.error ?? null,
    refreshThreads,
    refreshProjects,
    refreshKnowledgeStatus,
    refreshRuntimeState,
    refreshBundle,
    refreshObjectMemories,
    refreshAtlasUpdates,
    setProjects,
    setThreads,
    setObjectMemories,
    setAtlasUpdates
  };
}
