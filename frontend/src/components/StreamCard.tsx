import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  Group,
  NumberInput,
  Popover,
  Select,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import type {
  Campaign,
  Offer,
  OfferStats,
  OfferStatus,
  Stream,
  StreamOffer,
} from "../api";
import { api, offerLabel } from "../api";
import type { StatsByOffer } from "../stats";
import { statsKey } from "../stats";

const STATUS_LABEL: Record<OfferStatus, string> = {
  active: "Активен",
  removed: "Убран",
  missing: "Удалён в Keitaro",
};

const STATUS_COLOR: Record<OfferStatus, string> = {
  active: "green",
  removed: "gray",
  missing: "orange",
};

interface StreamCardProps {
  stream: Stream;
  stats: StatsByOffer;
  readableOffers: Offer[];
  busy: boolean;
  /** Runs an edit request; the parent shows errors and replaces the campaign with the answer. */
  onEdit: (request: () => Promise<Campaign>) => void;
}

export function StreamCard({
  stream,
  stats,
  readableOffers,
  busy,
  onEdit,
}: StreamCardProps) {
  return (
    <Card withBorder padding="md">
      <Stack gap="sm">
        <Group justify="space-between">
          <Group gap="xs">
            <Title order={4}>
              #{stream.position} {stream.name}
            </Title>
            <Badge variant="light" color={stream.is_editable ? "blue" : "gray"}>
              {stream.is_editable ? "офферы" : "редирект"}
            </Badge>
            {stream.has_unpushed_changes && (
              <Badge color="yellow">есть неотправленные изменения</Badge>
            )}
          </Group>
          <Text size="xs" c="dimmed">
            поток {stream.id}
          </Text>
        </Group>
        {stream.is_editable ? (
          <OffersTable
            stream={stream}
            stats={stats}
            readableOffers={readableOffers}
            busy={busy}
            onEdit={onEdit}
          />
        ) : (
          <Text size="sm" c="dimmed">
            Поток без офферов — редактировать нечего. Кликов за период:{" "}
            {stats.get(statsKey(stream.id, 0))?.clicks ?? 0}
          </Text>
        )}
      </Stack>
    </Card>
  );
}

function OffersTable({
  stream,
  stats,
  readableOffers,
  busy,
  onEdit,
}: StreamCardProps) {
  const [offerToAdd, setOfferToAdd] = useState<string | null>(null);
  const activeCount = stream.offers.filter(
    (offer) => offer.status === "active",
  ).length;
  const activeIds = new Set(
    stream.offers
      .filter((offer) => offer.status === "active")
      .map((offer) => offer.offer_id),
  );
  const addable = readableOffers
    .filter((offer) => !activeIds.has(offer.id))
    .map((offer) => ({
      value: String(offer.id),
      label: `${offer.id} — ${offer.name ?? ""}`,
    }));

  return (
    <Stack gap="sm">
      <Table.ScrollContainer minWidth={680}>
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Оффер</Table.Th>
              <Table.Th>Доля</Table.Th>
              <Table.Th>Статус</Table.Th>
              <Table.Th ta="right">Клики</Table.Th>
              <Table.Th ta="right">Уники</Table.Th>
              <Table.Th ta="right">Конверсии</Table.Th>
              <Table.Th ta="right">Доход</Table.Th>
              <Table.Th>Действия</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {stream.offers.map((offer) => (
              <OfferRow
                key={offer.offer_id}
                stream={stream}
                offer={offer}
                stats={stats.get(statsKey(stream.id, offer.offer_id))}
                isLastActive={offer.status === "active" && activeCount === 1}
                busy={busy}
                onEdit={onEdit}
              />
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <Group align="end">
        <Select
          label="Добавить оффер"
          placeholder={
            addable.length ? "Выберите оффер" : "Нет доступных офферов"
          }
          data={addable}
          value={offerToAdd}
          onChange={setOfferToAdd}
          searchable
          w={420}
          disabled={busy || addable.length === 0}
        />
        <Button
          disabled={busy || offerToAdd === null}
          onClick={() => {
            if (offerToAdd === null) return;
            onEdit(() => api.addOffer(stream.id, Number(offerToAdd)));
            setOfferToAdd(null);
          }}
        >
          Добавить
        </Button>
      </Group>
    </Stack>
  );
}

interface OfferRowProps {
  stream: Stream;
  offer: StreamOffer;
  stats: OfferStats | undefined;
  isLastActive: boolean;
  busy: boolean;
  onEdit: (request: () => Promise<Campaign>) => void;
}

function OfferRow({
  stream,
  offer,
  stats,
  isLastActive,
  busy,
  onEdit,
}: OfferRowProps) {
  const active = offer.status === "active";
  return (
    <Table.Tr style={active ? undefined : { opacity: 0.6 }}>
      <Table.Td>
        <Text size="sm">{offerLabel(offer)}</Text>
        <Text size="xs" c="dimmed">
          #{offer.offer_id}
        </Text>
      </Table.Td>
      <Table.Td>
        <Text size="sm" fw={500}>
          {offer.share}%{offer.is_pinned ? " 📌" : ""}
        </Text>
      </Table.Td>
      <Table.Td>
        <Badge variant="light" color={STATUS_COLOR[offer.status]}>
          {STATUS_LABEL[offer.status]}
        </Badge>
      </Table.Td>
      <Table.Td ta="right">{stats?.clicks ?? 0}</Table.Td>
      <Table.Td ta="right">{stats?.unique_clicks ?? 0}</Table.Td>
      <Table.Td ta="right">{stats?.conversions ?? 0}</Table.Td>
      <Table.Td ta="right">{(stats?.revenue ?? 0).toFixed(2)}</Table.Td>
      <Table.Td>
        <Group gap={6} wrap="nowrap">
          {active ? (
            <>
              <PinControl
                stream={stream}
                offer={offer}
                busy={busy}
                onEdit={onEdit}
              />
              <Button
                size="xs"
                variant="light"
                color="red"
                disabled={busy || isLastActive}
                title={
                  isLastActive
                    ? "Последний активный оффер убрать нельзя"
                    : undefined
                }
                onClick={() =>
                  onEdit(() => api.removeOffer(stream.id, offer.offer_id))
                }
              >
                Убрать
              </Button>
            </>
          ) : (
            <>
              <Button
                size="xs"
                variant="light"
                disabled={busy}
                onClick={() =>
                  onEdit(() => api.restoreOffer(stream.id, offer.offer_id))
                }
              >
                Вернуть
              </Button>
              <Button
                size="xs"
                variant="subtle"
                color="red"
                disabled={busy}
                onClick={() => {
                  if (
                    window.confirm(
                      `Удалить «${offerLabel(offer)}» из обёртки совсем?`,
                    )
                  ) {
                    onEdit(() => api.deleteOffer(stream.id, offer.offer_id));
                  }
                }}
              >
                Удалить полностью
              </Button>
            </>
          )}
        </Group>
      </Table.Td>
    </Table.Tr>
  );
}

interface PinControlProps {
  stream: Stream;
  offer: StreamOffer;
  busy: boolean;
  onEdit: (request: () => Promise<Campaign>) => void;
}

function PinControl({ stream, offer, busy, onEdit }: PinControlProps) {
  const [opened, setOpened] = useState(false);
  const [share, setShare] = useState<number | string>(offer.share);

  if (offer.is_pinned) {
    return (
      <Button
        size="xs"
        variant="light"
        disabled={busy}
        onClick={() =>
          onEdit(() => api.setPin(stream.id, offer.offer_id, null))
        }
      >
        Открепить
      </Button>
    );
  }
  return (
    <Popover opened={opened} onChange={setOpened} position="bottom" withArrow>
      <Popover.Target>
        <Button
          size="xs"
          variant="light"
          disabled={busy}
          onClick={() => {
            setShare(offer.share);
            setOpened((value) => !value);
          }}
        >
          Закрепить
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="xs">
          <NumberInput
            label="Доля, %"
            min={0}
            max={100}
            value={share}
            onChange={setShare}
            allowDecimal={false}
          />
          <Button
            size="xs"
            disabled={share === ""}
            onClick={() => {
              setOpened(false);
              onEdit(() =>
                api.setPin(stream.id, offer.offer_id, Number(share)),
              );
            }}
          >
            Закрепить долю
          </Button>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}
