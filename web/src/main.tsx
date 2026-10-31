import { render } from "preact";
import { App } from "./app";
import "./instrument/web/instrument.css";
import "./ds/ds.css";
import { loadFamilyApps, loadHealth, loadMe } from "./store";
import { applyStoredTheme } from "./theme";

// Before the first paint, so a saved Light or Dark never flashes the other.
applyStoredTheme();
void loadMe().then(loadFamilyApps);
void loadHealth();
render(<App />, document.getElementById("app")!);
