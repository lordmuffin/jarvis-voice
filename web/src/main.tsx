import { render } from "preact";
import { App } from "./app";
import { loadMe } from "./store";
import "./styles.css";

void loadMe();
render(<App />, document.getElementById("app")!);
