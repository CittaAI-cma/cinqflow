"use client";

import { useRef, useState } from "react";

/** Drag-and-drop or click-to-pick, wrapping a real file input so it posts with
 *  the form exactly as a native control would. */
export default function FileDropzone({
  name,
  accept,
  required,
  id,
  onPicked,
}: {
  name: string;
  accept?: string;
  required?: boolean;
  id?: string;
  onPicked?: (file: File | null) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);

  function adopt(file: File | null | undefined) {
    if (!file || !inputRef.current) return;
    const transfer = new DataTransfer();
    transfer.items.add(file);
    inputRef.current.files = transfer.files;
    setFileName(file.name);
    onPicked?.(file);
  }

  return (
    <div
      className={`dropzone${dragActive ? " active" : ""}`}
      onClick={() => inputRef.current?.click()}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
      }}
      onDragOver={(event) => {
        event.preventDefault();
        setDragActive(true);
      }}
      onDragLeave={() => setDragActive(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragActive(false);
        adopt(event.dataTransfer.files[0]);
      }}
      role="button"
      tabIndex={0}
    >
      <b>{fileName ? "File ready" : "Drop a CSV or XLSX here"}</b>
      <span>{fileName ? <span className="picked">{fileName}</span> : "or click to choose a file"}</span>
      <input
        ref={inputRef}
        id={id}
        name={name}
        type="file"
        accept={accept}
        required={required}
        style={{ display: "none" }}
        onChange={(event) => {
          const file = event.target.files?.[0] ?? null;
          setFileName(file?.name ?? null);
          onPicked?.(file);
        }}
      />
    </div>
  );
}
