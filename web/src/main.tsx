import { render } from "preact";
import { App } from "./app";
import { loadHealth, loadMe } from "./store";
import "./styles.css";

void loadMe();
void loadHealth();
render(<App />, document.getElementById("app")!);
