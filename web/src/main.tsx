import { render } from "preact";
import { App } from "./app";
import { loadChannel, loadMe } from "./store";
import "./styles.css";

void loadMe();
void loadChannel();
render(<App />, document.getElementById("app")!);
