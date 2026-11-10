use chrono::{Duration, Utc};
use echo::storage::{
    CharacterState, CharacterStateStore, ChatMessage, Database, NoteStore, ReminderStore, ShortTermMemory, TokenUsage,
    UsageStore,
};

#[tokio::test]
async fn short_term_memory_keeps_latest_messages() {
    let memory = ShortTermMemory::new(Database::in_memory().unwrap(), 4);
    for i in 0..3 {
        memory
            .add(1, vec![ChatMessage::new("user", format!("u{i}")), ChatMessage::new("assistant", format!("a{i}"))])
            .await
            .unwrap();
    }

    let contents: Vec<String> = memory.get(1).await.unwrap().into_iter().map(|m| m.content).collect();
    assert_eq!(contents, ["u1", "a1", "u2", "a2"]);
    assert!(memory.get(2).await.unwrap().is_empty());

    memory.clear(1).await.unwrap();
    assert!(memory.get(1).await.unwrap().is_empty());
}

#[tokio::test]
async fn character_state_roundtrip_and_decay() {
    let db = Database::in_memory().unwrap();
    let store = CharacterStateStore::new(db.clone());

    assert_eq!(store.get(1).await.unwrap(), CharacterState::default());
    assert!(store.last_interaction(1).await.unwrap().is_none());

    let state = CharacterState { affection: 10, mood: 50, stamina: 50, trust: 50 };
    store.save(1, state).await.unwrap();
    store.save(2, state).await.unwrap();
    assert_eq!(store.get(1).await.unwrap(), state);
    assert!(store.last_interaction(1).await.unwrap().is_some());

    db.call(|conn| {
        conn.execute("UPDATE character_state SET last_interaction_at = datetime('now', '-5 days') WHERE user_id = 1", [])
            .map(drop)
    })
    .await
    .unwrap();

    assert_eq!(store.decay(3, 2).await.unwrap(), 1);
    assert_eq!(store.decay(3, 2).await.unwrap(), 0); // 每天最多一次
    assert_eq!(store.get(1).await.unwrap().affection, 8);
    assert_eq!(store.get(2).await.unwrap().affection, 10);

    store.delete_user(1).await.unwrap();
    assert_eq!(store.get(1).await.unwrap(), CharacterState::default());
}

#[tokio::test]
async fn usage_is_accumulated_per_user() {
    let usage = UsageStore::new(Database::in_memory().unwrap());
    usage.record(1, TokenUsage { requests: 2, input_tokens: 100, output_tokens: 50 }).await.unwrap();
    usage.record(1, TokenUsage { requests: 1, input_tokens: 10, output_tokens: 5 }).await.unwrap();
    usage.record(2, TokenUsage { requests: 1, input_tokens: 1, output_tokens: 1 }).await.unwrap();

    let mine = usage.summary(Some(1), Some(1)).await.unwrap();
    assert_eq!((mine.messages, mine.requests, mine.total_tokens()), (2, 3, 165));
    assert_eq!(usage.summary(None, None).await.unwrap().messages, 3);
    assert_eq!(usage.top_users(30, 10).await.unwrap()[0].0, 1);
}

#[tokio::test]
async fn notes_overwrite_and_delete() {
    let notes = NoteStore::new(Database::in_memory().unwrap());
    notes.set(1, "生日".into(), "3 月 14 日".into()).await.unwrap();
    notes.set(1, "生日".into(), "3 月 15 日".into()).await.unwrap();
    notes.set(1, "喜歡的食物".into(), "布丁".into()).await.unwrap();

    let all = notes.get_all(1).await.unwrap();
    assert_eq!(all.len(), 2);
    assert!(all.contains(&("生日".to_owned(), "3 月 15 日".to_owned())));
    assert!(notes.delete(1, "生日".into()).await.unwrap());
    assert!(!notes.delete(1, "生日".into()).await.unwrap());
}

#[tokio::test]
async fn reminders_pop_when_due() {
    let store = ReminderStore::new(Database::in_memory().unwrap());
    let now = Utc::now();
    let id = store.add(1, 42, now + Duration::minutes(30), "該喝水囉！".into()).await.unwrap();
    store.add(1, 42, now + Duration::days(2), "之後".into()).await.unwrap();

    assert_eq!(store.count_user(1).await.unwrap(), 2);
    assert!(store.pop_due(now, 50).await.unwrap().is_empty());

    let due = store.pop_due(now + Duration::minutes(31), 50).await.unwrap();
    assert_eq!(due.len(), 1);
    assert_eq!((due[0].id, due[0].channel_id, due[0].content.as_str()), (id, 42, "該喝水囉！"));
    assert_eq!(store.count_user(1).await.unwrap(), 1);

    assert!(!store.cancel(2, id + 1).await.unwrap()); // 不能取消別人的提醒
    assert!(store.cancel(1, id + 1).await.unwrap());
}

#[tokio::test]
async fn opens_database_created_by_python_version() {
    // Python 版較舊的資料庫沒有 last_interaction_at／last_decay_at 欄位，開啟時應自動補上
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("data").join("memory.db");
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    {
        let conn = rusqlite::Connection::open(&path).unwrap();
        conn.execute_batch(
            "CREATE TABLE character_state (
                 user_id INTEGER PRIMARY KEY, affection INTEGER NOT NULL, mood INTEGER NOT NULL,
                 stamina INTEGER NOT NULL, trust INTEGER NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
             INSERT INTO character_state (user_id, affection, mood, stamina, trust) VALUES (7, 33, 44, 55, 66);",
        )
        .unwrap();
    }

    let store = CharacterStateStore::new(Database::open(&path).await.unwrap());
    assert_eq!(store.get(7).await.unwrap(), CharacterState { affection: 33, mood: 44, stamina: 55, trust: 66 });
    assert!(store.last_interaction(7).await.unwrap().is_some());
    assert_eq!(store.decay(3, 1).await.unwrap(), 0);
}
