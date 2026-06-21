// Shared types mirroring the backend contract (backend/app/models.py).
export type Category =
  | "urgent_medical"
  | "in_person_caregiver"
  | "routine_comfort"
  | "automated_task"
  | "family_communication"
  | "general_conversation";

export type Urgency = "emergency" | "high" | "medium" | "low";
export type Status = "new" | "in_progress" | "resolved";

export interface CareRequest {
  request_id: string;
  patient_id: string;
  patient_name: string;
  room_number: string;
  transcript: string;
  category: Category;
  urgency: Urgency;
  summary: string;
  suggested_action: string;
  requires_confirmation: boolean;
  status: Status;
  created_at: string;
  patient_context: string;
  confirmation_prompt: string | null;
  task_state: string | null;
  spoken_response: string | null;
}

export interface FamilyContact {
  name: string;
  relation: string;
  phone?: string | null;
  email?: string | null;
}

export interface PatientProfile {
  patient_id: string;
  name: string;
  room_number: string;
  age?: number;
  interests: string[];
  common_requests: string[];
  family_contacts: FamilyContact[];
}
