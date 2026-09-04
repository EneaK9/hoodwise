export type Citation = {
  kind?: string;
  spec_id?: string;
  part_name?: string;
  value_raw?: string;
  page_number?: number;
  doc_id?: string;
  section_name?: string;
  condition_note?: string;
  replace_required?: boolean;
  crop_path?: string | null;
};

export type VehicleInfo = {
  year?: number | string | null;
  make?: string | null;
  model?: string | null;
  trim?: string | null;
  body?: string | null;
  engine_label?: string | null;
  engine_family?: string | null;
  fuel?: string | null;
  fuel_source?: string | null;
  needs_fuel_confirmation?: boolean;
  transmission?: string | null;
  label?: string | null;
  variant_id?: string | null;
  confidence?: string | null;
  note?: string | null;
  source?: string | null;
  specs?: Array<{ label: string; value: string }>;
  displacement_l?: string | number | null;
  plant_country?: string | null;
  history?: {
    note?: string;
    stolen?: Array<{ region: string; status: string }>;
    stolen_note?: string;
    market?: {
      region?: string;
      currency?: string;
      median?: number;
      low?: number;
      high?: number;
      odometer_median_km?: number;
      sample_size?: number;
      markets?: string[];
    } | null;
    market_note?: string;
  } | null;
};

export type ChatResponse = {
  session_id: string;
  message_id: string;
  answer: string;
  refused: boolean;
  mode?: string;
  citations: Citation[];
  detected_vins: string[];
  vehicle?: VehicleInfo | null;
  model?: string | null;
  shop?: ShopLinks | null;
  clarify?: Clarify | null;
  needs_fuel?: boolean;
};

export type Clarify = { missing: string; options: string[] };

export type ShopLink = { name: string; url: string };

export type ShopLinks = {
  kind?: string;
  spec: string;
  query: string;
  note: string;
  source?: "manual" | "web" | "search";
  links: ShopLink[];
};

export type Variant = {
  variant_id: string;
  make: string;
  model: string;
  generation: string;
  year_from: number;
  year_to: number;
  engine_label: string;
  transmission: string;
  trim: string | null;
  body: string | null;
};

export type User = { id: string; email: string };

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  vehicle?: VehicleInfo | null;
  shop?: ShopLinks | null;
  clarify?: Clarify | null;
};
