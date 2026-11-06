# Ripple

Drop one problem. Watch the ripple. Fix only what changed.

An emergency co-pilot for event organizers, built on Cominty Managed Agents.

## Run the tests

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q
```

## Configure Cominty

```bash
cp backend/.env.example backend/.env   # then fill in your key, user id and the 4 agent ids
```
