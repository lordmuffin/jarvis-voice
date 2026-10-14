import { Segmented } from "../ds/components";
import { setTheme, theme, THEMES, type Theme } from "../theme";

const LABEL: Record<Theme, string> = { system: "System", light: "Light", dark: "Dark" };
const OPTIONS = THEMES.map((id) => ({ id, label: LABEL[id] }));

/** System / Light / Dark. System follows the OS and is the default. */
export function ThemeToggle() {
  return <Segmented label="Theme" value={theme.value} options={OPTIONS} onChange={setTheme} />;
}
