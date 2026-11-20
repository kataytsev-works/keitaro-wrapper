import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import {
  Alert,
  Button,
  Card,
  Loader,
  Select,
  Stack,
  TagsInput,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import type { CreatorOptions, NamedItem } from "../api";
import { api, errorText } from "../api";

// Suggestions only: any two-letter ISO code can be typed in.
const COUNTRY_SUGGESTIONS = [
  "AU",
  "BR",
  "CA",
  "DE",
  "ES",
  "FR",
  "GB",
  "ID",
  "IN",
  "IT",
  "JP",
  "KZ",
  "MX",
  "NL",
  "PE",
  "PL",
  "PT",
  "RO",
  "RU",
  "TH",
  "TR",
  "UA",
  "US",
  "VN",
];

function toOptions(items: NamedItem[]) {
  return items.map((item) => ({
    value: String(item.id),
    label: `${item.name} (${item.id})`,
  }));
}

/** The only option is picked right away: the tracker may give us exactly one domain or source. */
function onlyOption(items: NamedItem[]): string | null {
  return items.length === 1 ? String(items[0].id) : null;
}

export function CreateCampaignPage() {
  const navigate = useNavigate();
  const [options, setOptions] = useState<CreatorOptions | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [countries, setCountries] = useState<string[]>([]);
  const [offerId, setOfferId] = useState<string | null>(null);
  const [domainId, setDomainId] = useState<string | null>(null);
  const [groupId, setGroupId] = useState<string | null>(null);
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    api.creatorOptions().then(
      (loaded) => {
        setOptions(loaded);
        setDomainId(onlyOption(loaded.domains));
        setGroupId(onlyOption(loaded.groups));
        setSourceId(onlyOption(loaded.traffic_sources));
        setOfferId(
          onlyOption(
            loaded.offers.map((offer) => ({
              id: offer.id,
              name: offer.name ?? "",
            })),
          ),
        );
      },
      (error: unknown) => setLoadError(errorText(error)),
    );
  }, []);

  if (loadError)
    return (
      <Alert color="red" title="Не удалось загрузить справочники">
        {loadError}
      </Alert>
    );
  if (options === null) return <Loader />;

  const ready =
    name.trim() !== "" &&
    countries.length > 0 &&
    offerId !== null &&
    domainId !== null &&
    groupId !== null &&
    sourceId !== null;

  async function submit() {
    if (!ready) return;
    setCreating(true);
    try {
      const campaign = await api.createCampaign({
        name: name.trim(),
        countries,
        offer_id: Number(offerId),
        domain_id: Number(domainId),
        group_id: Number(groupId),
        traffic_source_id: Number(sourceId),
      });
      notifications.show({
        color: "green",
        message: `Кампания ${campaign.id} создана`,
      });
      navigate(`/campaigns/${campaign.id}`);
    } catch (error) {
      notifications.show({
        color: "red",
        title: "Кампания не создана",
        message: errorText(error),
      });
    } finally {
      setCreating(false);
    }
  }

  return (
    <Card withBorder padding="lg" maw={640}>
      <Stack gap="md">
        <Title order={3}>Новая кампания</Title>
        <Text size="sm" c="dimmed">
          Создаётся кампания с двумя потоками: посетители из выбранных стран
          уходят на Google, все остальные — на оффер.
        </Text>
        <TextInput
          label="Название"
          required
          value={name}
          onChange={(event) => setName(event.currentTarget.value)}
        />
        <TagsInput
          label="Гео (коды стран ISO-2)"
          description="Например AU, MX, RO — Enter после каждого кода"
          required
          data={COUNTRY_SUGGESTIONS}
          value={countries}
          onChange={(values) =>
            setCountries([
              ...new Set(
                values
                  .map((value) => value.trim().toUpperCase())
                  .filter((value) => /^[A-Z]{2}$/.test(value)),
              ),
            ])
          }
          clearable
        />
        <Select
          label="Оффер"
          required
          searchable
          data={options.offers.map((offer) => ({
            value: String(offer.id),
            label: `${offer.id} — ${offer.name ?? ""}`,
          }))}
          value={offerId}
          onChange={setOfferId}
        />
        <Select
          label="Домен"
          required
          data={toOptions(options.domains)}
          value={domainId}
          onChange={setDomainId}
        />
        <Select
          label="Группа"
          required
          searchable
          data={toOptions(options.groups)}
          value={groupId}
          onChange={setGroupId}
        />
        <Select
          label="Источник"
          required
          data={toOptions(options.traffic_sources)}
          value={sourceId}
          onChange={setSourceId}
        />
        <Button onClick={submit} loading={creating} disabled={!ready}>
          Создать
        </Button>
      </Stack>
    </Card>
  );
}
