SPIRE-Net CNN weights + training checkpoint
==============================================
Ship with the app under app/models/:

  spire_net_weights.npz           — active weights (frozen for normal Process)
  spire_net_weights.GOOD.npz      — last known-good backup
  spire_net_meta.json             — training meta
  spire_train_checkpoint.json     — overnight train resume state (history, best_loss)
  *.tmp.npz                       — optional temp copies from last save

Do not delete the primary .npz files when sharing the project.
Train launcher (repo root): Train_SPIRE_Background.command
