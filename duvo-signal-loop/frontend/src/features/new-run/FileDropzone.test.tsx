import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { FileDropzone } from "./FileDropzone";

const CSV = `name,domain,country,description
Acme,acme.com,US,Maker of anvils
Globex,globex.io,DE,Tech conglomerate
`;

function csvFile(text: string, name = "companies.csv") {
  return new File([text], name, { type: "text/csv" });
}

describe("FileDropzone", () => {
  it("uses the repo default (null companies) until a file is chosen", () => {
    const onChange = vi.fn();
    render(<FileDropzone onChange={onChange} companies={null} />);
    expect(screen.getByText(/repo default/i)).toBeInTheDocument();
  });

  it("parses a chosen companies.csv into Company[] and reports the row count", async () => {
    const onChange = vi.fn();
    render(<FileDropzone onChange={onChange} companies={null} />);

    const input = screen.getByLabelText(/companies\.csv/i);
    await userEvent.upload(input, csvFile(CSV));

    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith([
        { name: "Acme", domain: "acme.com", country: "US", description: "Maker of anvils" },
        { name: "Globex", domain: "globex.io", country: "DE", description: "Tech conglomerate" },
      ]),
    );
  });

  it("rejects a CSV missing required name/domain columns", async () => {
    const onChange = vi.fn();
    render(<FileDropzone onChange={onChange} companies={null} />);

    const input = screen.getByLabelText(/companies\.csv/i);
    await userEvent.upload(input, csvFile("foo,bar\n1,2\n"));

    expect(await screen.findByRole("alert")).toHaveTextContent(/name.*domain|domain.*name/i);
    expect(onChange).not.toHaveBeenCalled();
  });
});
