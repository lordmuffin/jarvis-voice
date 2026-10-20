import { loginUrl } from "../api";
import { Banner, Brand, Button, CenterCard, Text } from "../ds/components";
import { authError, releaseChannel } from "../store";

export function Login() {
  const error = authError.value;
  return (
    <CenterCard>
      <Brand heading channel={releaseChannel.value} />
      <Text as="p" size="body-lg" tone="muted">
        Sign in to watch your sessions.
      </Text>
      {error && <Banner variant="error">{error}</Banner>}
      <Button
        variant="primary"
        size="lg"
        onClick={() => {
          // A full page load: the server bounces through Pocket-ID and sets the cookie.
          location.href = loginUrl(location.pathname + location.search);
        }}
      >
        Sign In with Pocket-ID
      </Button>
    </CenterCard>
  );
}
