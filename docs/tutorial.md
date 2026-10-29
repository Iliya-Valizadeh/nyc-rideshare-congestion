# Tutorial

This page walks you through one full run of nyc-rideshare-congestion, from a fresh clone to
the demo output. You need `uv`, `make`, and the Python version named in
`pyproject.toml`. `uv` can install that Python for you.

## Step 1: get the code

```bash
git clone https://github.com/Iliya-Valizadeh/nyc-rideshare-congestion.git
cd nyc-rideshare-congestion
```

## Step 2: install

```bash
make setup
```

## Step 3: run the demo

```bash
make demo
```

The demo writes a fake zone list and two fake months of trips into a temporary folder.
The rows are made up, with a fixed seed. They are not real trips. Then it runs the real
pipeline commands on them, in this order:

1. `nrc init` creates the warehouse tables and fills the dimensions.
2. `nrc load --all` loads both months. Each month prints how many rows it kept and how
   many it quarantined, for each reason code.
3. `nrc load --all` runs again. Both months print `skip`, and no rows are added.
4. `nrc check` passes on the two loaded months.
5. `nrc check` runs again, one month past the data. It prints `FAIL` lines and exits
   with code 1. The demo expects this failure.
6. `nrc transform` builds the three report tables, and the demo prints their first
   rows.

The last line you should see is `demo finished: every step did what it should`. The
demo then deletes its temporary folder, so it leaves nothing in the repo.
