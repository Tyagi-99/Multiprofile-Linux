import { useState } from "react";
import { getHashQuery } from "../../lib/router";
import { Tabs, TabsList, TabsTrigger } from "../ui/tabs";
import { DiagnosticsTab } from "./diagnostics-tab";
import { GatewaysTab } from "./gateways-tab";
import { PoolsTab } from "./pools-tab";
import { ProxiesTab } from "./proxies-tab";

const TABS = ["proxies", "pools", "gateways", "diagnostics"] as const;
type TabId = (typeof TABS)[number];

export function NetworkPage({ initialTab }: { initialTab?: TabId }) {
  const [tab, setTab] = useState<TabId>(() => {
    if (initialTab) return initialTab;
    const t = getHashQuery().get("tab");
    return (TABS as readonly string[]).includes(t ?? "") ? (t as TabId) : "proxies";
  });

  const onTabChange = (t: string) => {
    const next = t as TabId;
    setTab(next);
    window.location.hash = `#/network?tab=${next}`;
  };

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-6 py-6">
      <h1 className="text-xl font-semibold tracking-tight text-ink-1">Network</h1>
      <Tabs value={tab} onValueChange={onTabChange}>
        <TabsList aria-label="Network sections">
          <TabsTrigger value="proxies">Proxies</TabsTrigger>
          <TabsTrigger value="pools">Pools</TabsTrigger>
          <TabsTrigger value="gateways">Gateways</TabsTrigger>
          <TabsTrigger value="diagnostics">Diagnostics</TabsTrigger>
        </TabsList>
      </Tabs>
      <div key={tab}>
        {tab === "proxies" && <ProxiesTab />}
        {tab === "pools" && <PoolsTab />}
        {tab === "gateways" && <GatewaysTab />}
        {tab === "diagnostics" && <DiagnosticsTab />}
      </div>
    </div>
  );
}
