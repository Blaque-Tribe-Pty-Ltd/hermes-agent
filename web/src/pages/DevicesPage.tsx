import { useCallback, useEffect, useState } from "react";
import { Check, Laptop, Clock } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { api } from "@/lib/api";
import type { MoonPieDevice } from "@/lib/api";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { errorMessage } from "@/lib/api-error";

export default function DevicesPage() {
  const [devices, setDevices] = useState<MoonPieDevice[]>([]);
  const [loading, setLoading] = useState(true);
  const [confirming, setConfirming] = useState<string | null>(null);
  const { toast, showToast } = useToast();

  const loadDevices = useCallback(() => {
    api
      .getMoonPieDevices()
      .then((res: MoonPieDevice[]) => {
        setDevices(res);
      })
      .catch(() => showToast("Failed to load devices", "error"))
      .finally(() => setLoading(false));
  }, [showToast]);

  useEffect(() => {
    loadDevices();
  }, [loadDevices]);

  const handleConfirm = async (device: MoonPieDevice) => {
    setConfirming(device.device_id);
    try {
      await api.confirmMoonPieDevice(device.device_id);
      showToast(`Confirmed: "${device.name}"`, "success");
      loadDevices();
    } catch (e) {
      showToast(
        `Could not confirm device: ${errorMessage(e)}`,
        "error"
      );
    } finally {
      setConfirming(null);
    }
  };

  const pending = devices.filter((d) => !d.confirmed);
  const confirmed = devices.filter((d) => d.confirmed);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="text-2xl text-primary" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <Toast toast={toast} />

      {/* Pending devices */}
      <div className="flex flex-col gap-3">
        <H2
          variant="sm"
          className="flex items-center gap-2 text-muted-foreground"
        >
          <Clock className="h-4 w-4" />
          Pending devices ({pending.length})
        </H2>

        {pending.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No pending devices
            </CardContent>
          </Card>
        )}

        {pending.map((device) => (
          <Card key={device.device_id}>
            <CardContent className="flex items-start gap-4 py-4">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <Badge tone="outline">{device.model || "Mac"}</Badge>
                  <span className="font-medium text-sm truncate">
                    {device.name}
                  </span>
                </div>
                <div className="flex items-center gap-4 text-xs text-muted-foreground">
                  <span className="truncate font-mono">{device.device_id}</span>
                  {device.os_version && (
                    <span>{device.os_version}</span>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-1 shrink-0">
                <Button
                  size="sm"
                  className="uppercase"
                  onClick={() => handleConfirm(device)}
                  disabled={confirming === device.device_id}
                  prefix={
                    confirming === device.device_id ? (
                      <Spinner />
                    ) : (
                      <Check className="h-4 w-4" />
                    )
                  }
                >
                  Confirm
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Confirmed devices */}
      <div className="flex flex-col gap-3">
        <H2
          variant="sm"
          className="flex items-center gap-2 text-muted-foreground"
        >
          <Laptop className="h-4 w-4" />
          Confirmed devices ({confirmed.length})
        </H2>

        {confirmed.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No confirmed devices
            </CardContent>
          </Card>
        )}

        {confirmed.map((device) => (
          <Card key={device.device_id}>
            <CardContent className="flex items-start gap-4 py-4">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <Badge tone="outline">{device.model || "Mac"}</Badge>
                  <span className="font-medium text-sm truncate">
                    {device.name}
                  </span>
                </div>
                <div className="flex items-center gap-4 text-xs text-muted-foreground">
                  <span className="truncate font-mono">{device.device_id}</span>
                  {device.os_version && (
                    <span>{device.os_version}</span>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}