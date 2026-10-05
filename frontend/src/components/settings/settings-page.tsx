import { useState } from "react";
import { getHashQuery } from "../../lib/router";
import { Tabs, TabsList, TabsTrigger } from "../ui/tabs";
import { BackupsTab, ExtensionsTab } from "./data-tabs";
import { GeneralTab } from "./general-tab";
import { AccountTab } from "./account-tab";
import { GroupsTab } from "./groups-tab";
import { TokensTab } from "./tokens-tab";

const TABS = ["general", "groups", "backups", "extensions", "tokens", "account"] as const;
type TabId = (typeof TABS)[number];

export function SettingsPage({ initialTab }: { initialTab?: TabId }) {
  const [tab, setTab] = useState<TabId>(() => {
    if (initialTab) return initialTab;
    const t = getHashQuery().get("tab");
    return (TABS as readonly string[]).includes(t ?? "") ? (t as TabId) : "general";
  });

  const onTabChange = (t: string) => {
    const next = t as TabId;
    setTab(next);
    window.location.hash = `#/settings?tab=${next}`;
  };

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-6 py-6">
      <h1 className="text-xl font-semibold tracking-tight text-ink-1">Settings</h1>
      <Tabs value={tab} onValueChange={onTabChange}>
        <TabsList aria-label="Settings sections">
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="groups">Clients &amp; Templates</TabsTrigger>
          <TabsTrigger value="backups">Backups</TabsTrigger>
          <TabsTrigger value="extensions">Extensions</TabsTrigger>
          <TabsTrigger value="tokens">API tokens</TabsTrigger>
          <TabsTrigger value="account">Your data</TabsTrigger>
        </TabsList>
      </Tabs>
      <div key={tab}>
        {tab === "general" && <GeneralTab />}
        {tab === "groups" && <GroupsTab />}
        {tab === "backups" && <BackupsTab />}
        {tab === "extensions" && <ExtensionsTab />}
        {tab === "tokens" && <TokensTab />}
        {tab === "account" && <AccountTab />}
      </div>
    </div>
  );
}
