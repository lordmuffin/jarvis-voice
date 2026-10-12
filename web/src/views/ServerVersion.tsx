import { serverBuild } from "../store";

/** The version of the server this dashboard is running on (e.g. v0.16.0), with the commit on
 * hover so a lab build of main can be told apart from the release it follows. */
export function ServerVersion() {
  const build = serverBuild.value;
  if (!build || build.version === "unknown") return null;
  const commit = build.commit ? ` (commit ${build.commit})` : "";
  return (
    <span class="version meta" data-testid="server-version" title={`Server version ${build.version}${commit}`}>
      v{build.version}
    </span>
  );
}
