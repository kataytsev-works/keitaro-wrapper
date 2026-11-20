import { AppShell, Anchor, Container, Group, Title } from "@mantine/core";
import { Link, Route, Routes } from "react-router";
import { CampaignEditorPage } from "./pages/CampaignEditorPage";
import { CampaignsPage } from "./pages/CampaignsPage";

export default function App() {
  return (
    <AppShell header={{ height: 56 }} padding="md">
      <AppShell.Header>
        <Container size="xl" h="100%">
          <Group h="100%" justify="space-between">
            <Anchor component={Link} to="/" underline="never" c="inherit">
              <Title order={3}>Keitaro Wrapper</Title>
            </Anchor>
            <Group>
              <Anchor component={Link} to="/">
                Кампании
              </Anchor>
            </Group>
          </Group>
        </Container>
      </AppShell.Header>
      <AppShell.Main>
        <Container size="xl">
          <Routes>
            <Route path="/" element={<CampaignsPage />} />
            <Route
              path="/campaigns/:campaignId"
              element={<CampaignEditorPage />}
            />
          </Routes>
        </Container>
      </AppShell.Main>
    </AppShell>
  );
}
