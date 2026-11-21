import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import {
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Select,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import type { CampaignSummary, KeitaroCampaign } from "../api";
import { api, errorText } from "../api";

export function CampaignsPage() {
  const navigate = useNavigate();
  const [campaigns, setCampaigns] = useState<CampaignSummary[] | null>(null);
  const [keitaroCampaigns, setKeitaroCampaigns] = useState<KeitaroCampaign[]>(
    [],
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);

  useEffect(() => {
    const showError = (error: unknown) =>
      notifications.show({
        color: "red",
        title: "Ошибка загрузки",
        message: errorText(error),
      });
    api.campaigns().then(setCampaigns, showError);
    api.keitaroCampaigns().then(setKeitaroCampaigns, showError);
  }, []);

  const options = useMemo(
    () =>
      keitaroCampaigns.map((campaign) => ({
        value: String(campaign.id),
        label: `${campaign.id} — ${campaign.name}`,
      })),
    [keitaroCampaigns],
  );

  /** Open the chosen campaign; it is imported only when the wrapper does not have it yet. */
  async function importSelected() {
    if (selected === null) return;
    if (campaigns?.some((campaign) => campaign.id === Number(selected))) {
      navigate(`/campaigns/${selected}`);
      return;
    }
    setImporting(true);
    try {
      const campaign = await api.importCampaign(Number(selected));
      navigate(`/campaigns/${campaign.id}`);
    } catch (error) {
      notifications.show({
        color: "red",
        title: "Импорт не удался",
        message: errorText(error),
      });
    } finally {
      setImporting(false);
    }
  }

  return (
    <Stack gap="lg">
      <Card withBorder padding="md">
        <Stack gap="sm">
          <Title order={3}>Открыть кампанию из Keitaro</Title>
          <Text size="sm" c="dimmed">
            Кампания копируется в обёртку вместе с потоками и офферами. Правки
            делаются здесь и отправляются в Keitaro кнопкой «Отправить».
          </Text>
          <Group align="end">
            <Select
              label="Кампания"
              placeholder="Начните вводить ID или название"
              data={options}
              value={selected}
              onChange={setSelected}
              searchable
              limit={50}
              w={480}
              nothingFoundMessage="Не найдено"
            />
            <Button
              onClick={importSelected}
              loading={importing}
              disabled={selected === null}
            >
              Открыть в редакторе
            </Button>
          </Group>
        </Stack>
      </Card>

      <Stack gap="sm">
        <Title order={3}>Кампании в обёртке</Title>
        {campaigns !== null && campaigns.length === 0 && (
          <Text c="dimmed">
            Пока пусто — откройте кампанию из Keitaro или создайте новую.
          </Text>
        )}
        {campaigns !== null && campaigns.length > 0 && (
          <Table striped withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>ID</Table.Th>
                <Table.Th>Название</Table.Th>
                <Table.Th>Обновлено из Keitaro</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {campaigns.map((campaign) => (
                <Table.Tr key={campaign.id}>
                  <Table.Td>{campaign.id}</Table.Td>
                  <Table.Td>
                    <Anchor component={Link} to={`/campaigns/${campaign.id}`}>
                      {campaign.name}
                    </Anchor>{" "}
                    {campaign.has_unpushed_changes && (
                      <Badge color="yellow">черновик</Badge>
                    )}
                  </Table.Td>
                  <Table.Td>
                    {new Date(campaign.synced_at).toLocaleString()}
                  </Table.Td>
                  <Table.Td>
                    <Anchor component={Link} to={`/campaigns/${campaign.id}`}>
                      Открыть
                    </Anchor>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>
    </Stack>
  );
}
