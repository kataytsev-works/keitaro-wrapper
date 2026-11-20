import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Loader,
  SegmentedControl,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import type { Campaign, Offer, StatsPeriod } from "../api";
import { api, errorText } from "../api";
import { StreamCard } from "../components/StreamCard";
import type { StatsByOffer } from "../stats";
import { statsKey } from "../stats";

export function CampaignEditorPage() {
  const campaignId = Number(useParams().campaignId);
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [readableOffers, setReadableOffers] = useState<Offer[]>([]);
  const [period, setPeriod] = useState<StatsPeriod>("today");
  const [stats, setStats] = useState<StatsByOffer>(new Map());
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .campaign(campaignId)
      .then(setCampaign, (error: unknown) => setLoadError(errorText(error)));
    api.offers().then(setReadableOffers, (error: unknown) =>
      notifications.show({
        color: "red",
        title: "Офферы",
        message: errorText(error),
      }),
    );
  }, [campaignId]);

  const loadStats = useCallback(() => {
    api.stats(campaignId, period).then(
      (rows) =>
        setStats(
          new Map(
            rows.map((row) => [statsKey(row.stream_id, row.offer_id), row]),
          ),
        ),
      (error: unknown) =>
        notifications.show({
          color: "red",
          title: "Статистика",
          message: errorText(error),
        }),
    );
  }, [campaignId, period]);

  useEffect(loadStats, [loadStats]);

  /** Run a request that answers with the campaign: lock the buttons, show errors, update the view. */
  const run = useCallback(
    async (request: () => Promise<Campaign>, success?: string) => {
      setBusy(true);
      try {
        setCampaign(await request());
        if (success) notifications.show({ color: "green", message: success });
      } catch (error) {
        notifications.show({
          color: "red",
          title: "Не получилось",
          message: errorText(error),
        });
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  if (loadError) {
    return (
      <Alert color="red" title="Кампания не загружена">
        {loadError}.{" "}
        <Anchor component={Link} to="/">
          К списку кампаний
        </Anchor>
      </Alert>
    );
  }
  if (campaign === null) return <Loader />;

  const streams = [...campaign.streams].sort((a, b) => a.position - b.position);

  return (
    <Stack gap="md">
      <Group justify="space-between" align="start">
        <Stack gap={2}>
          <Group gap="xs">
            <Title order={2}>{campaign.name}</Title>
            {campaign.has_unpushed_changes && (
              <Badge color="yellow">черновик</Badge>
            )}
          </Group>
          <Text size="sm" c="dimmed">
            Кампания {campaign.id} · обновлено из Keitaro{" "}
            {new Date(campaign.synced_at).toLocaleString()}
          </Text>
        </Stack>
        <Group>
          <Button
            variant="default"
            disabled={busy || campaign.has_unpushed_changes}
            title={
              campaign.has_unpushed_changes
                ? "Сначала отправьте или отмените изменения"
                : undefined
            }
            onClick={() =>
              run(() => api.refresh(campaign.id), "Обновлено из Keitaro")
            }
          >
            Обновить из Keitaro
          </Button>
          <Button
            variant="default"
            color="red"
            disabled={busy || !campaign.has_unpushed_changes}
            onClick={() => {
              if (window.confirm("Отменить все неотправленные изменения?")) {
                run(() => api.discard(campaign.id), "Изменения отменены");
              }
            }}
          >
            Отменить изменения
          </Button>
          <Button
            disabled={busy || !campaign.has_unpushed_changes}
            loading={busy}
            onClick={() =>
              run(() => api.push(campaign.id), "Отправлено в Keitaro")
            }
          >
            Отправить в Keitaro
          </Button>
        </Group>
      </Group>

      <Group gap="xs">
        <Text size="sm">Статистика за:</Text>
        <SegmentedControl
          size="xs"
          value={period}
          onChange={(value) => setPeriod(value as StatsPeriod)}
          data={[
            { value: "today", label: "сегодня" },
            { value: "yesterday", label: "вчера" },
            { value: "7d", label: "7 дней" },
          ]}
        />
        <Button size="xs" variant="subtle" onClick={loadStats}>
          Обновить статистику
        </Button>
      </Group>

      {streams.map((stream) => (
        <StreamCard
          key={stream.id}
          stream={stream}
          stats={stats}
          readableOffers={readableOffers}
          busy={busy}
          onEdit={(request) => void run(request)}
        />
      ))}
    </Stack>
  );
}
