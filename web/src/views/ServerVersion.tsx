import { Text } from "../ds/components";
import { serverBuild } from "../store";

/** The footer's version line, as every family app prints it: app, server version and short
 * commit (e.g. "jarvis-live v0.16.0 · abc1234"), so a lab build of main can be told apart from
 * the release it follows. */
export function ServerVersion() {
  const build = serverBuild.value;
  if (!build || build.version === "unknown") return null;
  const commit = build.commit ? ` · ${build.commit}` : "";
  return (
    <Text size="mono" data-testid="server-version">
      jarvis-live v{build.version}
      {commit}
    </Text>
  );
}
